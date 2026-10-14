"""Durable report outbox and evidence-backed improvement actions.

This module dispatches the existing BI graph. It does not execute arbitrary SQL,
create another agent, or interpret an observed improvement as an intervention effect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from dataagent.artifacts import ArtifactStore, canonical_json, fingerprint
from dataagent.bi.contracts import BIPlan, BIRequest, Contract
from dataagent.bi.tasks import TERMINAL
from dataagent.database import Database
from dataagent.errors import ConflictError, DataAgentError, NotFoundError
from dataagent.models import Base
from dataagent.schemas import GraphRequest

logger = logging.getLogger(__name__)
RUN_TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "TIMED_OUT", "CANCELLED", "DISPATCH_UNCERTAIN"}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ScheduleSpec(Contract):
    name: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=6000)
    plan: BIPlan
    frequency: Literal["daily", "weekly", "monthly"] = "daily"
    timezone: str = "Asia/Shanghai"
    hour: int = Field(8, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    timeout_seconds: int = Field(600, ge=30, le=7200)
    max_attempts: int = Field(2, ge=1, le=3)
    retry_delay_seconds: int = Field(60, ge=10, le=3600)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown timezone; install tzdata on Windows if needed") from exc
        return value

    @model_validator(mode="after")
    def dynamic_calendar_scope(self) -> ScheduleSpec:
        if self.plan.filters.get("start_date") or self.plan.filters.get("end_date"):
            raise ValueError("Schedule dates are resolved from its completed calendar period")
        return self


class EvidencePoint(Contract):
    run_id: str
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    metric_id: str = Field(min_length=1, max_length=80)
    row_index: int = Field(0, ge=0)


class ActionSpec(Contract):
    title: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=120)
    due_date: date
    baseline: EvidencePoint
    target: Decimal
    acceptance: Literal["at_most", "at_least", "equal"]
    acceptance_note: str = Field(min_length=1, max_length=2000)

    @field_validator("target")
    @classmethod
    def finite_target(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Target must be a finite measurable value")
        return value


class ActionReview(Contract):
    actual: EvidencePoint
    reviewed_by: str = Field(min_length=1, max_length=120)
    review_note: str = Field(min_length=1, max_length=2000)
    approved: bool


class ReportSchedule(Base):
    __tablename__ = "report_schedule_v1"
    schedule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    spec_json: Mapped[str] = mapped_column(Text)
    metric_contract_json: Mapped[str] = mapped_column(Text)
    definition_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReportRun(Base):
    __tablename__ = "report_run_v1"
    __table_args__ = (
        UniqueConstraint("schedule_id", "period_key", "definition_hash", name="uq_report_period_v1"),
    )
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    period_key: Mapped[str] = mapped_column(String(40))
    definition_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    spec_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    attempt_history_json: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str] = mapped_column(Text, default="")
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReportAction(Base):
    __tablename__ = "report_action_v1"
    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    owner: Mapped[str] = mapped_column(String(120))
    due_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    spec_json: Mapped[str] = mapped_column(Text)
    baseline_json: Mapped[str] = mapped_column(Text)
    review_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def _previous_boundary(end: date, frequency: str) -> date:
    if frequency == "daily":
        return end - timedelta(days=1)
    if frequency == "weekly":
        return end - timedelta(days=7)
    return (end - timedelta(days=1)).replace(day=1)


def completed_period(spec: ScheduleSpec, now: datetime, period_end: date | None = None) -> dict[str, str]:
    """Calendar interval [start, end); PdM adapter takes inclusive date filters."""
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(spec.timezone))
    end = period_end or local.date()
    if period_end is None:
        if spec.frequency == "weekly":
            end -= timedelta(days=end.weekday())
        elif spec.frequency == "monthly":
            end = end.replace(day=1)
        due = datetime.combine(end, time(spec.hour, spec.minute), ZoneInfo(spec.timezone))
        if local < due:
            end = _previous_boundary(end, spec.frequency)
    if (spec.frequency == "weekly" and end.weekday() != 0) or (spec.frequency == "monthly" and end.day != 1):
        raise DataAgentError("period_end must be a Monday/month boundary for this frequency")
    if end > local.date():
        raise DataAgentError("Reports can only use completed periods")
    start = _previous_boundary(end, spec.frequency)
    return {
        "start_date": start.isoformat(),
        "end_date": (end - timedelta(days=1)).isoformat(),
        "end_exclusive": end.isoformat(),
        "timezone": spec.timezone,
        "period_key": f"{spec.frequency}:{start.isoformat()}:{end.isoformat()}",
    }


class ReportOperations:
    def __init__(
        self,
        database: Database,
        graph: Any,
        *,
        enabled: bool = False,
        poll_interval_seconds: float = 10,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.db, self.graph, self.enabled, self.clock = database, graph, enabled, clock
        self.poll_interval_seconds = max(1, poll_interval_seconds)
        self.store = ArtifactStore(graph.settings)
        self._runner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.enabled and self._runner is None:
            self._runner = asyncio.create_task(self._loop())

    async def close(self) -> None:
        if self._runner:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self._runner is not None and not self._runner.done(),
            "poll_interval_seconds": self.poll_interval_seconds,
            "dispatch_policy": (
                "One task per durable attempt; uncertain dispatch is never retried automatically"
            ),
            "refresh_policy": (
                "GET is read-only; POST refresh reconciles and may dispatch a due confirmed-terminal retry"
            ),
        }

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Report scheduler polling failed; durable runs will be reconciled")
            await asyncio.sleep(self.poll_interval_seconds)

    def _contracts(self, spec: ScheduleSpec) -> list[dict[str, Any]]:
        adapter = self.graph.catalog.get(spec.plan.dataset_id)
        if adapter.definition().time_kind != "calendar":
            raise DataAgentError("Relative-time datasets cannot be scheduled as calendar reports")
        if not {"start_date", "end_date"}.issubset(adapter.filter_model.model_fields):
            raise DataAgentError("Dataset does not support explicit calendar period filters")
        adapter.validate(spec.plan)
        known = {m.metric_id: m for m in adapter.metrics()}
        # Include excluded executable expression in the version fingerprint.
        return [
            {**known[key].model_dump(mode="json"), "expression": known[key].expression}
            for key in spec.plan.metric_ids
        ]

    async def create_schedule(self, agent_id: int, spec: ScheduleSpec) -> dict[str, Any]:
        contracts = self._contracts(spec)
        encoded = spec.model_dump(mode="json")
        row = ReportSchedule(
            schedule_id=str(uuid4()),
            agent_id=agent_id,
            status="ACTIVE",
            spec_json=canonical_json(encoded),
            metric_contract_json=canonical_json(contracts),
            definition_hash=fingerprint({"spec": encoded, "metric_contracts": contracts}),
            created_at=self.clock(),
            updated_at=self.clock(),
        )
        async with self.db.session_factory() as session:
            session.add(row)
            await session.commit()
        return self._schedule_view(row)

    @staticmethod
    def _schedule_view(row: ReportSchedule) -> dict[str, Any]:
        return {
            "schedule_id": row.schedule_id,
            "agent_id": row.agent_id,
            "status": row.status,
            "spec": json.loads(row.spec_json),
            "definition_hash": row.definition_hash,
            "metric_contracts": json.loads(row.metric_contract_json),
            "catch_up_policy": (
                "latest completed due period only; historical periods require explicit trigger"
            ),
        }

    async def _schedule(self, schedule_id: str, agent_id: int) -> ReportSchedule:
        async with self.db.session_factory() as session:
            row = await session.get(ReportSchedule, schedule_id)
            if row is None or row.agent_id != agent_id:
                raise NotFoundError("Report schedule not found for this agent")
            return row

    async def schedules(self, agent_id: int) -> list[dict[str, Any]]:
        async with self.db.session_factory() as session:
            rows = (
                await session.scalars(
                    select(ReportSchedule)
                    .where(ReportSchedule.agent_id == agent_id)
                    .order_by(ReportSchedule.created_at.desc())
                )
            ).all()
        return [self._schedule_view(row) for row in rows]

    async def set_paused(self, schedule_id: str, agent_id: int, paused: bool) -> dict[str, Any]:
        await self._schedule(schedule_id, agent_id)
        async with self.db.session_factory() as session:
            await session.execute(
                update(ReportSchedule)
                .where(ReportSchedule.schedule_id == schedule_id, ReportSchedule.agent_id == agent_id)
                .values(status="PAUSED" if paused else "ACTIVE", updated_at=self.clock())
            )
            await session.commit()
        return self._schedule_view(await self._schedule(schedule_id, agent_id))

    async def trigger(
        self,
        schedule_id: str,
        agent_id: int,
        period_end: date | None = None,
    ) -> dict[str, Any]:
        schedule = await self._schedule(schedule_id, agent_id)
        spec = ScheduleSpec.model_validate_json(schedule.spec_json)
        period = completed_period(spec, self.clock(), period_end)
        run_id = str(
            uuid5(NAMESPACE_URL, f"report:{schedule_id}:{period['period_key']}:{schedule.definition_hash}")
        )
        plan = spec.plan.model_copy(deep=True)
        plan.filters.update(start_date=period["start_date"], end_date=period["end_date"])
        resolved = {
            "schedule": spec.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "period": period,
            "metric_contracts": json.loads(schedule.metric_contract_json),
            "requested_data_cutoff": period["end_exclusive"],
            "snapshot_notice": "Event-date cutoff is not a transactional source snapshot",
        }
        async with self.db.session_factory() as session:
            if await session.get(ReportRun, run_id) is None:
                session.add(
                    ReportRun(
                        run_id=run_id,
                        schedule_id=schedule_id,
                        agent_id=agent_id,
                        period_key=period["period_key"],
                        definition_hash=schedule.definition_hash,
                        spec_json=canonical_json(resolved),
                        status="PENDING",
                        attempts=0,
                        next_attempt_at=self.clock(),
                        created_at=self.clock(),
                        updated_at=self.clock(),
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()  # Another worker inserted the same deterministic run.
        await self._advance(run_id)
        return await self.run(run_id, agent_id, advance=False)

    async def run(self, run_id: str, agent_id: int, *, advance: bool = False) -> dict[str, Any]:
        row = await self._run(run_id, agent_id)
        if advance:
            await self._advance(run_id)
            row = await self._run(run_id, agent_id)
        return {
            "run_id": row.run_id,
            "schedule_id": row.schedule_id,
            "agent_id": row.agent_id,
            "status": row.status,
            "attempts": row.attempts,
            "task_id": row.task_id,
            "spec": json.loads(row.spec_json),
            "result": json.loads(row.result_json),
            "error": row.error,
            "definition_hash": row.definition_hash,
            "attempt_history": json.loads(row.attempt_history_json),
            "next_attempt_at": row.next_attempt_at.isoformat(),
        }

    async def refresh_run(self, run_id: str, agent_id: int) -> dict[str, Any]:
        return await self.run(run_id, agent_id, advance=True)

    async def runs(self, agent_id: int, schedule_id: str | None = None) -> list[dict[str, Any]]:
        if schedule_id:
            await self._schedule(schedule_id, agent_id)
        async with self.db.session_factory() as session:
            query = select(ReportRun.run_id).where(ReportRun.agent_id == agent_id)
            if schedule_id:
                query = query.where(ReportRun.schedule_id == schedule_id)
            ids = (await session.scalars(query.order_by(ReportRun.created_at.desc()).limit(100))).all()
        return [await self.run(identifier, agent_id, advance=False) for identifier in ids]

    async def _run(self, run_id: str, agent_id: int | None = None) -> ReportRun:
        async with self.db.session_factory() as session:
            row = await session.get(ReportRun, run_id)
            if row is None or (agent_id is not None and row.agent_id != agent_id):
                raise NotFoundError("Report run not found for this agent")
            return row

    async def tick(self) -> None:
        async with self.db.session_factory() as session:
            schedules = (
                await session.scalars(select(ReportSchedule).where(ReportSchedule.status == "ACTIVE"))
            ).all()
            runs = (
                await session.scalars(select(ReportRun.run_id).where(ReportRun.status.not_in(RUN_TERMINAL)))
            ).all()
        for row in schedules:
            try:
                await self.trigger(row.schedule_id, row.agent_id)
            except DataAgentError:
                logger.exception("Cannot resolve report schedule %s", row.schedule_id)
        for run_id in runs:
            await self._advance(run_id)

    async def _save_state(self, row: ReportRun, **values: Any) -> bool:
        async with self.db.session_factory() as session:
            changed = await session.execute(
                update(ReportRun)
                .where(
                    ReportRun.run_id == row.run_id,
                    ReportRun.status == row.status,
                    ReportRun.attempts == row.attempts,
                    ReportRun.task_id == row.task_id,
                    ReportRun.updated_at == row.updated_at,
                )
                .values(**values, updated_at=self.clock())
            )
            await session.commit()
            return bool(changed.rowcount)

    async def _terminal(
        self, row: ReportRun, status: str, error: str, result: dict[str, Any], *, allow_retry: bool = True
    ) -> None:
        spec = ScheduleSpec.model_validate(json.loads(row.spec_json)["schedule"])
        retry = allow_retry and status in {"FAILED", "TIMED_OUT"} and row.attempts < spec.max_attempts
        history = json.loads(row.attempt_history_json)
        history.append(
            {
                "attempt": row.attempts,
                "task_id": row.task_id,
                "status": status,
                "finished_at": self.clock().isoformat() + "Z",
                "error": error[:4000],
                "artifacts": result.get("artifacts", []),
            }
        )
        await self._save_state(
            row,
            status="RETRY" if retry else status,
            error=error[:4000],
            result_json=canonical_json(result),
            lease_token=None,
            lease_until=None,
            attempt_history_json=canonical_json(history),
            next_attempt_at=self.clock() + timedelta(seconds=spec.retry_delay_seconds),
        )

    async def _advance(self, run_id: str) -> None:
        row = await self._run(run_id)
        if row.status in RUN_TERMINAL:
            return
        if row.status in {"RUNNING", "WAITING", "CANCELLING", "DISPATCHING"}:
            try:
                task = await self.graph.tasks.get(row.task_id, row.agent_id)
            except NotFoundError:
                if row.status == "DISPATCHING" and row.lease_until and row.lease_until <= self.clock():
                    # Never re-dispatch this uncertain attempt after a worker crash.
                    await self._terminal(
                        row,
                        "DISPATCH_UNCERTAIN",
                        "Dispatcher stopped before durable graph task appeared; "
                        "no automatic retry because the old dispatcher may still register it",
                        {},
                    )
                elif row.deadline and row.deadline <= self.clock():
                    await self._terminal(
                        row,
                        "DISPATCH_UNCERTAIN",
                        "Durable graph task disappeared before completion; old worker liveness is unknown",
                        {},
                    )
                return
            if task["status"] in TERMINAL:
                result = {"task": task, "artifacts": []}
                try:
                    result["artifacts"] = self.store.load(row.task_id, row.agent_id)["artifacts"]
                except NotFoundError:
                    pass
                status = task["status"]
                heartbeat_uncertain = "Worker heartbeat expired" in task.get("error", "")
                if status == "COMPLETED" and not {"query_result", "report_input", "answer"}.issubset(
                    {item["kind"] for item in result["artifacts"]}
                ):
                    status = "PARTIAL"
                    result["publication_notice"] = (
                        "Graph completed without all report evidence/final-answer artifacts; "
                        "report publication is not confirmed"
                    )
                if row.status == "CANCELLING":
                    status = "TIMED_OUT"
                if heartbeat_uncertain:
                    # A missing heartbeat proves no progress, not worker death. Fencing is conservative.
                    status = "DISPATCH_UNCERTAIN"
                await self._terminal(row, status, task.get("error", ""), result)
            elif row.deadline and self.clock() >= row.deadline:
                await self.graph.tasks.cancel(row.task_id, row.agent_id)
                await self.graph.stop(thread_id=row.task_id, conversation_id=None)
                await self._save_state(
                    row, status="CANCELLING", error="Report deadline exceeded; awaiting graph termination"
                )
            elif row.status == "DISPATCHING" and row.lease_until and row.lease_until <= self.clock():
                # A task exists: adopt it, rather than launch it again.
                spec = ScheduleSpec.model_validate(json.loads(row.spec_json)["schedule"])
                await self._save_state(
                    row,
                    status="RUNNING",
                    lease_token=None,
                    lease_until=None,
                    deadline=self.clock() + timedelta(seconds=spec.timeout_seconds),
                )
            elif task["status"] == "WAITING" and row.status == "RUNNING":
                await self._save_state(row, status="WAITING")
            elif task["status"] in {"PENDING", "RUNNING"} and row.status == "WAITING":
                await self._save_state(row, status="RUNNING")
            return
        if row.status not in {"PENDING", "RETRY"} or row.next_attempt_at > self.clock():
            return
        token = str(uuid4())
        task_id = str(uuid5(NAMESPACE_URL, f"report-task:{row.run_id}:{row.attempts + 1}"))
        claimed = await self._save_state(
            row,
            status="DISPATCHING",
            attempts=row.attempts + 1,
            task_id=task_id,
            lease_token=token,
            lease_until=self.clock() + timedelta(seconds=30),
            deadline=None,
        )
        if not claimed:
            return
        row = await self._run(run_id)
        payload = json.loads(row.spec_json)
        spec = ScheduleSpec.model_validate(payload["schedule"])
        try:
            if self._contracts(spec) != payload["metric_contracts"]:
                raise ConflictError("Pinned metric contract changed; create a reviewed new schedule")
            plan = BIPlan.model_validate(payload["plan"])
            adapter = self.graph.catalog.get(plan.dataset_id)
            adapter.validate(plan)
            # The watermark is observed, not inferred from the wall clock. Refuse stale periods.
            data_range = getattr(getattr(adapter, "service", None), "data_range", None)
            if data_range is None:
                raise DataAgentError("Dataset cannot provide a calendar data watermark")
            available_start, available_end = await data_range()
            period = payload["period"]
            if available_start > date.fromisoformat(
                period["start_date"]
            ) or available_end < date.fromisoformat(period["end_date"]):
                raise DataAgentError("Source calendar watermark does not cover the requested report period")
            payload["data_watermark"] = {
                "available_start_date": available_start.isoformat(),
                "available_end_date": available_end.isoformat(),
                "observed_at": self.clock().isoformat() + "Z",
                "coverage_notice": "Dataset-wide date coverage; not a per-table completeness guarantee",
            }
            if not await self._save_state(row, spec_json=canonical_json(payload)):
                return
            row = await self._run(run_id)
            if row.status != "DISPATCHING" or row.lease_token != token or row.lease_until <= self.clock():
                return
            request = BIRequest(
                agent_id=row.agent_id,
                question=spec.question,
                dataset_id=plan.dataset_id,
                filters=plan.filters,
                plan=plan,
                report=True,
                conversation_id=task_id,
            )
            dispatched = await self.graph.start_bi(
                GraphRequest(
                    agent_id=str(row.agent_id),
                    query=spec.question,
                    thread_id=task_id,
                    conversation_id=task_id,
                    bi_request=request.model_dump(mode="json"),
                ),
                reserved_task_id=task_id,
            )
            if dispatched["task_id"] != task_id:
                raise RuntimeError("Graph did not honor its internal reserved task identifier")
            await self._save_state(
                row,
                status="RUNNING",
                lease_token=None,
                lease_until=None,
                deadline=self.clock() + timedelta(seconds=spec.timeout_seconds),
            )
        except (DataAgentError, ValueError) as exc:
            # Definition/watermark validation errors are not transient; no blind retries.
            await self._terminal(row, "FAILED", str(exc), {}, allow_retry=False)
        except Exception as exc:
            # start_bi may have committed a task before failing: preserve dispatch uncertainty.
            await self._save_state(row, error=f"Dispatch uncertain: {exc}"[:4000])

    def _evidence_value(self, point: EvidencePoint, agent_id: int) -> dict[str, Any]:
        artifact = self.store.get(point.run_id, agent_id, point.artifact_id)
        if artifact["kind"] != "query_result":
            raise DataAgentError("Action measurements require a registered BI query_result metric cell")
        content = artifact["content"]
        contracts = {m["metric_id"]: m for m in content.get("metric_definitions", [])}
        if point.metric_id not in contracts or content.get("truncated"):
            raise DataAgentError("Action measurement needs an untruncated registered metric result")
        try:
            result_row = content["data"][point.row_index]
            raw = result_row[point.metric_id]
            if raw is None or isinstance(raw, bool):
                raise ValueError
            value = Decimal(str(raw))
            if not value.is_finite():
                raise ValueError
        except (KeyError, IndexError, ValueError, InvalidOperation, TypeError) as exc:
            raise DataAgentError("Action evidence must locate a finite numeric metric cell") from exc
        manifest = self.store.load(point.run_id, agent_id)
        if manifest["status"] not in {"succeeded", "completed"}:
            raise DataAgentError("Action evidence must come from a finished analysis run")
        scope = content.get("scope", content.get("source", {}).get("scope"))
        if not isinstance(scope, dict):
            raise DataAgentError("Action evidence requires an explicit comparable calendar scope")
        dataset_id = content.get("source", {}).get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise DataAgentError("Action evidence requires a registered dataset identity")
        self.graph.catalog.get(dataset_id)
        try:
            start = date.fromisoformat(scope.get("start_date", scope.get("startDate", "")))
            end = date.fromisoformat(scope.get("end_date", scope.get("endDate", "")))
            if start > end:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DataAgentError(
                "Action evidence requires explicit start/end dates for exposure comparison"
            ) from exc
        if end >= self.clock().date():
            raise DataAgentError(
                "Action measurement needs a completed calendar window, not a partial/current/future day"
            )
        non_temporal_scope = {
            k: v
            for k, v in scope.items()
            if k not in {"start_date", "end_date", "startDate", "endDate"} and v is not None
        }
        return {
            "point": point.model_dump(),
            "value": str(value),
            "sha256": artifact["sha256"],
            "metric_contract": contracts[point.metric_id],
            "contract_hash": fingerprint(contracts[point.metric_id]),
            "dataset_id": dataset_id,
            "scope": scope,
            "non_temporal_scope": non_temporal_scope,
            "period": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "duration_days": (end - start).days + 1,
            },
            "row_dimensions": {k: v for k, v in result_row.items() if k not in contracts},
        }

    async def create_action(self, agent_id: int, spec: ActionSpec) -> dict[str, Any]:
        baseline = self._evidence_value(spec.baseline, agent_id)
        row = ReportAction(
            action_id=str(uuid4()),
            agent_id=agent_id,
            owner=spec.owner,
            due_date=spec.due_date.isoformat(),
            status="OPEN",
            spec_json=canonical_json(spec.model_dump(mode="json")),
            baseline_json=canonical_json(baseline),
            review_json="{}",
            created_at=self.clock(),
            updated_at=self.clock(),
        )
        async with self.db.session_factory() as session:
            session.add(row)
            await session.commit()
        return self._action_view(row)

    @staticmethod
    def _action_view(row: ReportAction) -> dict[str, Any]:
        return {
            "action_id": row.action_id,
            "agent_id": row.agent_id,
            "status": row.status,
            "owner": row.owner,
            "due_date": row.due_date,
            "spec": json.loads(row.spec_json),
            "baseline": json.loads(row.baseline_json),
            "review": json.loads(row.review_json),
            "attribution_notice": "Measured target attainment is not proof of a causal intervention benefit",
            "identity_notice": (
                "Reviewer name is declared under the agent credential, not an independently verified identity"
            ),
        }

    async def actions(self, agent_id: int) -> list[dict[str, Any]]:
        async with self.db.session_factory() as session:
            rows = (
                await session.scalars(
                    select(ReportAction)
                    .where(ReportAction.agent_id == agent_id)
                    .order_by(ReportAction.created_at.desc())
                )
            ).all()
        return [self._action_view(row) for row in rows]

    async def review_action(self, action_id: str, agent_id: int, review: ActionReview) -> dict[str, Any]:
        async with self.db.session_factory() as session:
            row = await session.get(ReportAction, action_id)
            if row is None or row.agent_id != agent_id:
                raise NotFoundError("Action not found for this agent")
            if row.status == "CLOSED":
                raise ConflictError("Closed actions are immutable; create a new action for a new target")
            if review.reviewed_by != row.owner:
                raise DataAgentError("Review must identify the recorded action owner")
            actual = self._evidence_value(review.actual, agent_id)
            baseline = json.loads(row.baseline_json)
            current_baseline = self._evidence_value(EvidencePoint.model_validate(baseline["point"]), agent_id)
            if current_baseline != baseline:
                raise DataAgentError("Baseline evidence changed; do not accept this action")
            if (
                actual["contract_hash"],
                actual["dataset_id"],
                actual["row_dimensions"],
                actual["non_temporal_scope"],
            ) != (
                baseline["contract_hash"],
                baseline["dataset_id"],
                baseline["row_dimensions"],
                baseline["non_temporal_scope"],
            ):
                raise DataAgentError(
                    "Actual measurement does not match the baseline "
                    "metric/dataset/dimensions/non-temporal filters"
                )
            if actual["period"]["duration_days"] != baseline["period"]["duration_days"]:
                raise DataAgentError("Actual and baseline windows must have equal calendar duration")
            if actual["period"]["start_date"] <= baseline["period"]["end_date"]:
                raise DataAgentError("Actual window must be later than and non-overlapping with baseline")
            if review.actual == EvidencePoint.model_validate(baseline["point"]):
                raise DataAgentError("Actual result must be new evidence, not the baseline cell")
            spec = ActionSpec.model_validate_json(row.spec_json)
            value = Decimal(actual["value"])
            target = spec.target
            met = (
                value <= target
                if spec.acceptance == "at_most"
                else value >= target
                if spec.acceptance == "at_least"
                else value == target
            )
            measured = {
                "actual": actual,
                "difference_from_baseline": str(value - Decimal(baseline["value"])),
                "target_met": met,
                "human_review": review.model_dump(mode="json"),
                "scope_comparability": (
                    "Owner must review exposure, workload and period differences; "
                    "no automatic causal attribution"
                ),
            }
            desired_status = "CLOSED" if review.approved and met else "REVIEWED"
            changed = await session.execute(
                update(ReportAction)
                .where(
                    ReportAction.action_id == action_id,
                    ReportAction.agent_id == agent_id,
                    ReportAction.status != "CLOSED",
                    ReportAction.updated_at == row.updated_at,
                    ReportAction.review_json == row.review_json,
                )
                .values(status=desired_status, review_json=canonical_json(measured), updated_at=self.clock())
            )
            if not changed.rowcount:
                raise ConflictError("Action changed concurrently; reload before reviewing")
            await session.commit()
            await session.refresh(row)
            return self._action_view(row)
