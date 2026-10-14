from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from dataagent.bi.catalog import IndustrialPDMAdapter
from dataagent.bi.contracts import BIPlan, MetricDefinition, PdmFilters
from dataagent.bi.tasks import TaskManager
from dataagent.dashboard import DashboardService
from dataagent.database import Database
from dataagent.errors import ConflictError, DataAgentError, NotFoundError
from dataagent.models import Agent
from dataagent.report_operations import (
    ActionReview,
    ActionSpec,
    EvidencePoint,
    ReportOperations,
    ReportRun,
    ScheduleSpec,
    completed_period,
)
from dataagent.security import hash_api_key


class FakeAdapter:
    filter_model = PdmFilters
    dataset_id = "industrial_pdm"

    def __init__(self):
        self.service = self
        self.description = "Executed count of failure events"
        self.available_start, self.available_end = date(2020, 1, 1), date(2030, 1, 1)

    def definition(self):
        return SimpleNamespace(time_kind="calendar", name="Calendar test dataset", entity_key="machineId")

    def metrics(self):
        return [
            MetricDefinition(
                metric_id="failure_count",
                name="Failure events",
                description=self.description,
                relation="failures",
                expression="COUNT(*)",
            )
        ]

    def validate(self, plan):
        if plan.metric_ids != ["failure_count"]:
            raise DataAgentError("Unknown metric")
        return PdmFilters.model_validate(plan.filters)

    async def data_range(self):
        return self.available_start, self.available_end

    async def query(self, plan):
        return {
            "column": ["failure_count"],
            "data": [{"failure_count": 4}],
            "truncated": False,
            "metric_definitions": [m.model_dump(mode="json") for m in self.metrics()],
            "source": {"dataset_id": self.dataset_id, "tables": ["failures"], "scope": plan.filters},
            "sql": "SELECT COUNT(*) AS failure_count FROM failures",
            "parameters": [],
            "limitations": [],
        }


class FakeGraph:
    def __init__(self, settings, database):
        self.settings = settings
        self.tasks = TaskManager(database)
        self.adapter = FakeAdapter()
        self.catalog = SimpleNamespace(get=lambda _: self.adapter)
        self.calls = []
        self.stopped = []

    async def start_bi(self, request, *, reserved_task_id=None):
        assert reserved_task_id == request.thread_id
        self.calls.append(request)
        await asyncio.sleep(0)
        await self.tasks.create(request.thread_id, int(request.agent_id), request.conversation_id)
        return {"task_id": request.thread_id, "status": "PENDING"}

    async def stop(self, *, thread_id, conversation_id):
        self.stopped.append(thread_id)
        await self.tasks.cancel(thread_id)


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 13, 12)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
async def operations(settings):
    database = Database(settings)
    await database.create_schema()
    clock = Clock()
    graph = FakeGraph(settings, database)
    service = ReportOperations(database, graph, clock=clock)
    yield service, graph, clock
    await service.close()
    await database.close()


def spec(**kwargs):
    return ScheduleSpec(
        name="Daily failure report",
        question="Generate evidence report",
        plan=BIPlan(dataset_id="industrial_pdm", metric_ids=["failure_count"]),
        **kwargs,
    )


def test_period_timezone_due_calendar_and_historical_boundaries():
    period = completed_period(spec(), datetime(2026, 10, 13, 0))  # Shanghai 08:00 exactly.
    assert period["start_date"] == "2026-10-12"
    assert period["end_date"] == "2026-10-12"
    assert completed_period(spec(), datetime(2026, 10, 12, 23, 59))["start_date"] == "2026-10-11"
    weekly = completed_period(spec(frequency="weekly"), datetime(2026, 10, 13, 0))
    assert (weekly["start_date"], weekly["end_exclusive"]) == ("2026-10-05", "2026-10-12")
    monthly = completed_period(spec(frequency="monthly"), datetime(2026, 3, 1, 0))
    assert (monthly["start_date"], monthly["end_date"]) == ("2026-02-01", "2026-02-28")
    with pytest.raises(DataAgentError, match="completed"):
        completed_period(spec(), datetime(2026, 10, 13, 0), date(2026, 10, 14))
    with pytest.raises(DataAgentError, match="Monday"):
        completed_period(spec(frequency="weekly"), datetime(2026, 10, 13, 0), date(2026, 10, 13))
    with pytest.raises(ValidationError):
        spec(timezone="Missing/Timezone")
    with pytest.raises(ValidationError, match="resolved"):
        ScheduleSpec(
            name="Fixed",
            question="Report",
            plan=BIPlan(
                dataset_id="industrial_pdm",
                metric_ids=["failure_count"],
                filters={"start_date": "2026-01-01"},
            ),
        )


async def test_concurrent_trigger_deduplicates_and_uses_existing_graph(operations):
    service, graph, _ = operations
    schedule = await service.create_schedule(1, spec())
    results = await asyncio.gather(*[service.trigger(schedule["schedule_id"], 1) for _ in range(8)])
    assert len({run["run_id"] for run in results}) == 1
    assert len(graph.calls) == 1
    run = await service.run(results[0]["run_id"], 1)
    assert run["status"] == "RUNNING"
    assert run["attempts"] == 1
    request = graph.calls[0]
    assert request.bi_request["report"] is True
    assert request.bi_request["filters"] == {"start_date": "2026-10-12", "end_date": "2026-10-12"}
    assert run["spec"]["data_watermark"]["available_end_date"] == "2030-01-01"
    await service.trigger(schedule["schedule_id"], 1)
    assert len(graph.calls) == 1


async def test_scope_ownership_pause_and_opt_in(operations):
    service, graph, _ = operations
    assert service._runner is None
    await service.start()
    assert service._runner is None
    schedule = await service.create_schedule(1, spec())
    await service.set_paused(schedule["schedule_id"], 1, True)
    await service.tick()
    assert graph.calls == []
    assert await service.schedules(2) == []
    with pytest.raises(NotFoundError):
        await service.trigger(schedule["schedule_id"], 2)
    run = await service.trigger(schedule["schedule_id"], 1)  # Explicit manual trigger is still available.
    with pytest.raises(NotFoundError):
        await service.run(run["run_id"], 2)
    assert await service.runs(2) == []


async def test_metric_drift_and_stale_watermark_fail_closed(operations):
    service, graph, _ = operations
    schedule = await service.create_schedule(1, spec())
    graph.adapter.description = "Changed denominator"
    run = await service.trigger(schedule["schedule_id"], 1)
    assert run["status"] == "FAILED"
    assert "contract changed" in run["error"]
    assert graph.calls == []
    schedule = await service.create_schedule(1, spec())
    graph.adapter.available_end = date(2026, 10, 11)
    run = await service.trigger(schedule["schedule_id"], 1)
    assert run["status"] == "FAILED"
    assert "watermark" in run["error"]
    assert graph.calls == []
    graph.adapter.definition = lambda: SimpleNamespace(time_kind="relative")
    with pytest.raises(DataAgentError, match="Relative"):
        await service.create_schedule(1, spec())


async def test_failure_retry_is_bounded_and_preserves_distinct_attempts(operations):
    service, graph, clock = operations
    schedule = await service.create_schedule(1, spec(max_attempts=2, retry_delay_seconds=10))
    run = await service.trigger(schedule["schedule_id"], 1)
    first = run["task_id"]
    await graph.tasks.finish(first, "FAILED", "Temporary model failure")
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "RETRY"
    await service.refresh_run(run["run_id"], 1)
    assert len(graph.calls) == 1
    clock.advance(11)
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "RUNNING" and run["attempts"] == 2
    assert run["task_id"] != first
    await graph.tasks.finish(run["task_id"], "FAILED", "Still failing")
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "FAILED"
    assert [a["status"] for a in run["attempt_history"]] == ["FAILED", "FAILED"]
    clock.advance(100)
    await service.refresh_run(run["run_id"], 1)
    assert len(graph.calls) == 2


async def test_timeout_waits_for_terminal_cancellation_before_retry(operations):
    service, graph, clock = operations
    schedule = await service.create_schedule(1, spec(timeout_seconds=30, retry_delay_seconds=10))
    run = await service.trigger(schedule["schedule_id"], 1)
    clock.advance(31)
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "CANCELLING"
    assert graph.stopped == [run["task_id"]]
    clock.advance(20)
    run = await service.refresh_run(run["run_id"], 1)
    assert len(graph.calls) == 1
    assert run["status"] == "CANCELLING"
    await graph.tasks.finish(run["task_id"], "CANCELLED")
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "RETRY"
    clock.advance(11)
    run = await service.refresh_run(run["run_id"], 1)
    assert len(graph.calls) == 2


async def test_restart_recovers_existing_dispatch_without_relaunch(operations):
    service, graph, clock = operations
    schedule = await service.create_schedule(1, spec())
    run = await service.trigger(schedule["schedule_id"], 1)
    async with service.db.session_factory() as session:
        await session.execute(
            update(ReportRun)
            .where(ReportRun.run_id == run["run_id"])
            .values(
                status="DISPATCHING", lease_token=str(uuid4()), lease_until=clock() - timedelta(seconds=1)
            )
        )
        await session.commit()
    restarted = ReportOperations(service.db, graph, clock=clock)
    run = await restarted.refresh_run(run["run_id"], 1)
    assert run["status"] == "RUNNING" and len(graph.calls) == 1
    restarted.store.create(1, "Evidence report", run["task_id"])
    for kind in ["query_result", "report_input", "answer"]:
        restarted.store.add(run["task_id"], 1, kind, {"kind": kind})
    await graph.tasks.finish(run["task_id"], "COMPLETED")
    run = await restarted.refresh_run(run["run_id"], 1)
    assert run["status"] == "COMPLETED"
    assert run["result"]["task"]["task_id"] == run["task_id"]
    assert (await restarted.schedules(1))[0]["definition_hash"] == schedule["definition_hash"]


async def test_completed_graph_without_final_report_is_partial(operations):
    service, graph, _ = operations
    schedule = await service.create_schedule(1, spec())
    run = await service.trigger(schedule["schedule_id"], 1)
    await graph.tasks.finish(run["task_id"], "COMPLETED")
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "PARTIAL"
    assert "not confirmed" in run["result"]["publication_notice"]


async def test_human_review_wait_is_not_retried(operations):
    service, graph, _ = operations
    schedule = await service.create_schedule(1, spec())
    run = await service.trigger(schedule["schedule_id"], 1)
    await graph.tasks.finish(run["task_id"], "WAITING")
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "WAITING"
    await service.trigger(schedule["schedule_id"], 1)
    assert len(graph.calls) == 1
    await graph.tasks.running(run["task_id"], "BI_EXECUTE_NODE", 50)
    assert (await service.refresh_run(run["run_id"], 1))["status"] == "RUNNING"


def metric_evidence(
    service,
    value,
    *,
    agent_id=1,
    truncated=False,
    model="A",
    metric_unit="count",
    start_date="2026-09-01",
    end_date="2026-09-07",
    machine_id=None,
):
    run_id = str(uuid4())
    service.store.create(agent_id, "Measure a registered metric", run_id)
    artifact = service.store.add(
        run_id,
        agent_id,
        "query_result",
        {
            "column": ["model", "failure_count"],
            "data": [{"model": model, "failure_count": value}],
            "metric_definitions": [{"metric_id": "failure_count", "version": "1", "unit": metric_unit}],
            "source": {"dataset_id": "industrial_pdm"},
            "scope": {"start_date": start_date, "end_date": end_date, "machine_id": machine_id},
            "truncated": truncated,
        },
    )
    service.store.finish(run_id, agent_id, "succeeded")
    return EvidencePoint(run_id=run_id, artifact_id=artifact["artifactId"], metric_id="failure_count")


async def test_action_closes_only_on_measured_target_and_owner_review(operations):
    service, _, _ = operations
    baseline = metric_evidence(service, 10)
    action = await service.create_action(
        1,
        ActionSpec(
            title="Reduce failure events",
            owner="设备负责人",
            due_date=date(2026, 10, 30),
            baseline=baseline,
            target=5,
            acceptance="at_most",
            acceptance_note="相同设备和暴露条件下故障次数不超过 5，负责人确认可比性",
        ),
    )
    actual = metric_evidence(service, 7, start_date="2026-09-08", end_date="2026-09-14")
    review = ActionReview(
        actual=actual, reviewed_by="设备负责人", review_note="Exposure reviewed", approved=True
    )
    action = await service.review_action(action["action_id"], 1, review)
    assert action["status"] == "REVIEWED"
    assert action["review"]["target_met"] is False
    actual = metric_evidence(service, 4, start_date="2026-09-08", end_date="2026-09-14")
    review = review.model_copy(update={"actual": actual, "approved": False})
    assert (await service.review_action(action["action_id"], 1, review))["status"] == "REVIEWED"
    review = review.model_copy(update={"approved": True})
    action = await service.review_action(action["action_id"], 1, review)
    assert action["status"] == "CLOSED"
    assert action["review"]["difference_from_baseline"] == "-6"
    assert "not proof" in action["attribution_notice"]
    with pytest.raises(ConflictError):
        await service.review_action(action["action_id"], 1, review)
    assert await service.actions(2) == []


async def test_action_rejects_unregistered_null_truncated_foreign_or_incomparable_evidence(operations):
    service, _, _ = operations
    baseline = metric_evidence(service, 10)
    payload = ActionSpec(
        title="Target",
        owner="Owner",
        due_date=date(2026, 10, 30),
        baseline=baseline,
        target=5,
        acceptance="at_most",
        acceptance_note="Review equal exposure",
    )
    for bad in [metric_evidence(service, None), metric_evidence(service, 3, truncated=True)]:
        with pytest.raises(DataAgentError):
            await service.create_action(1, payload.model_copy(update={"baseline": bad}))
    with pytest.raises(NotFoundError):
        await service.create_action(2, payload)
    action = await service.create_action(1, payload)
    for actual in [
        baseline,
        metric_evidence(service, 4, model="B"),
        metric_evidence(service, 4, metric_unit="%"),
    ]:
        with pytest.raises(DataAgentError):
            await service.review_action(
                action["action_id"],
                1,
                ActionReview(actual=actual, reviewed_by="Owner", review_note="Reviewed", approved=True),
            )
    with pytest.raises(DataAgentError, match="owner"):
        await service.review_action(
            action["action_id"],
            1,
            ActionReview(
                actual=metric_evidence(service, 4), reviewed_by="Other", review_note="Reviewed", approved=True
            ),
        )
    with pytest.raises(ValidationError):
        payload.model_validate({**payload.model_dump(), "target": "NaN"})


async def test_action_cannot_meet_target_by_narrowing_scope_or_shortening_period(operations):
    service, _, _ = operations
    payload = ActionSpec(
        title="Target",
        owner="Owner",
        due_date=date(2026, 10, 30),
        baseline=metric_evidence(service, 10),
        target=5,
        acceptance="at_most",
        acceptance_note="Review equal exposure",
    )
    action = await service.create_action(1, payload)
    cases = [
        metric_evidence(service, 2, machine_id=1, start_date="2026-09-08", end_date="2026-09-14"),
        metric_evidence(service, 2, start_date="2026-09-08", end_date="2026-09-09"),
        metric_evidence(service, 2, start_date="2026-09-05", end_date="2026-09-11"),
        metric_evidence(service, 2, start_date="2026-08-08", end_date="2026-08-14"),
        metric_evidence(service, 2, start_date="2026-10-10", end_date="2026-10-16"),
    ]
    for actual in cases:
        with pytest.raises(DataAgentError):
            await service.review_action(
                action["action_id"],
                1,
                ActionReview(actual=actual, reviewed_by="Owner", review_note="Reviewed", approved=True),
            )
    assert (await service.actions(1))[0]["status"] == "OPEN"


async def test_get_run_does_not_reconcile_or_dispatch_retry(operations):
    service, graph, clock = operations
    schedule = await service.create_schedule(1, spec(retry_delay_seconds=10))
    run = await service.trigger(schedule["schedule_id"], 1)
    await graph.tasks.finish(run["task_id"], "FAILED")
    assert (await service.run(run["run_id"], 1))["status"] == "RUNNING"
    assert (await service.refresh_run(run["run_id"], 1))["status"] == "RETRY"
    clock.advance(11)
    assert (await service.run(run["run_id"], 1))["status"] == "RETRY"
    assert len(graph.calls) == 1
    assert service.status()["enabled"] is False
    assert service.status()["running"] is False


async def test_uncertain_dispatch_is_terminal_even_when_old_task_registers_late(operations):
    service, graph, clock = operations

    async def delayed_dispatch(request, *, reserved_task_id=None):
        graph.calls.append(request)
        raise RuntimeError("Worker lost connection before confirming task creation")

    graph.start_bi = delayed_dispatch
    schedule = await service.create_schedule(1, spec(retry_delay_seconds=10))
    run = await service.trigger(schedule["schedule_id"], 1)
    assert run["status"] == "DISPATCHING"
    clock.advance(31)
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "DISPATCH_UNCERTAIN"
    # The old dispatcher wakes late. It must not overlap an automatically launched new attempt.
    await graph.tasks.create(run["task_id"], 1, run["task_id"])
    clock.advance(100)
    await service.refresh_run(run["run_id"], 1)
    await service.trigger(schedule["schedule_id"], 1)
    assert len(graph.calls) == 1
    assert (await service.run(run["run_id"], 1))["status"] == "DISPATCH_UNCERTAIN"


async def test_heartbeat_expiry_is_not_proof_of_worker_death(operations):
    service, graph, clock = operations
    schedule = await service.create_schedule(1, spec(retry_delay_seconds=10))
    run = await service.trigger(schedule["schedule_id"], 1)
    await graph.tasks.finish(run["task_id"], "FAILED", "Worker heartbeat expired; retry the task")
    run = await service.refresh_run(run["run_id"], 1)
    assert run["status"] == "DISPATCH_UNCERTAIN"
    clock.advance(100)
    await service.refresh_run(run["run_id"], 1)
    assert len(graph.calls) == 1


async def test_api_requires_agent_credentials_and_get_is_read_only(app, client):
    async with app.state.database.session_factory() as session:
        agent = Agent(name="Report owner", api_key_enabled=1, api_key=hash_api_key("test-report-key"))
        other = Agent(name="Different report owner")
        session.add_all([agent, other])
        await session.commit()
        owner_id, other_id = agent.id, other.id
    clock = Clock()
    graph = FakeGraph(app.state.settings, app.state.database)
    app.state.report_operations = ReportOperations(app.state.database, graph, clock=clock)
    params = {"agent_id": owner_id}
    headers = {"X-API-Key": "test-report-key"}
    assert (await client.get("/api/report-operations/status", params=params)).status_code == 401
    response = await client.get("/api/report-operations/status", params=params, headers=headers)
    assert response.status_code == 200 and response.json()["enabled"] is False
    response = await client.post(
        "/api/report-operations/schedules",
        params=params,
        headers=headers,
        json=spec().model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    schedule_id = response.json()["schedule_id"]
    response = await client.post(
        f"/api/report-operations/schedules/{schedule_id}/trigger", params=params, headers=headers, json={}
    )
    assert response.status_code == 202, response.text
    run_id, task_id = response.json()["run_id"], response.json()["task_id"]
    await graph.tasks.finish(task_id, "FAILED", "Temporary failure")
    response = await client.get(f"/api/report-operations/runs/{run_id}", params=params, headers=headers)
    assert response.json()["status"] == "RUNNING"
    response = await client.post(
        f"/api/report-operations/runs/{run_id}/refresh", params=params, headers=headers
    )
    assert response.json()["status"] == "RETRY"
    response = await client.get(f"/api/report-operations/runs/{run_id}", params={"agent_id": other_id})
    assert response.status_code == 404
    response = await client.post(
        "/api/report-operations/schedules",
        params=params,
        headers=headers,
        json={**spec().model_dump(mode="json"), "sql": "DELETE FROM failures"},
    )
    assert response.status_code == 400  # Existing application validation handler normalizes 422 to 400.


async def test_real_graph_honors_reserved_report_task_id_and_generates_evidence(app):
    async with app.state.database.session_factory() as session:
        agent = Agent(name="Periodic evidence report", status="published")
        session.add(agent)
        await session.commit()
        agent_id = agent.id
    app.state.graph.catalog.adapters["industrial_pdm"] = FakeAdapter()
    service = ReportOperations(app.state.database, app.state.graph, clock=Clock())
    schedule = await service.create_schedule(agent_id, spec())
    run = await service.trigger(schedule["schedule_id"], agent_id)
    for _ in range(100):
        await asyncio.sleep(0.05)
        run = await service.refresh_run(run["run_id"], agent_id)
        if run["status"] in {"COMPLETED", "PARTIAL", "FAILED", "DISPATCH_UNCERTAIN"}:
            break
    assert run["status"] == "COMPLETED", run
    assert run["result"]["task"]["task_id"] == run["task_id"]
    assert {"query_result", "report_input", "answer"}.issubset(
        {a["kind"] for a in run["result"]["artifacts"]}
    )
    assert run["result"]["task"]["result"]["answer"]
    await app.state.graph.close()


async def test_real_pdm_adapter_preflight_observes_fresh_dashboard_watermark(operations, monkeypatch):
    service, graph, _ = operations
    dashboard = DashboardService(graph.settings)
    observed = AsyncMock(
        side_effect=[
            (date(2020, 1, 1), date(2026, 10, 11)),
            (date(2020, 1, 1), date(2026, 10, 12)),
            (date(2020, 1, 1), date(2026, 10, 13)),
        ]
    )
    monkeypatch.setattr(dashboard.repository, "data_range", observed)
    try:
        # Keep an older populated scope cache. Scheduling must observe the source,
        # rather than mistake a cached range for the current report watermark.
        assert await dashboard._data_range() == (date(2020, 1, 1), date(2026, 10, 11))
        adapter = IndustrialPDMAdapter(dashboard)
        graph.catalog = SimpleNamespace(get=lambda _: adapter)
        schedule = await service.create_schedule(1, spec())
        run = await service.trigger(schedule["schedule_id"], 1)
        assert run["status"] == "RUNNING", run
        assert run["spec"]["data_watermark"]["available_end_date"] == "2026-10-12"
        assert observed.await_count == 2
        assert len(graph.calls) == 1
        assert await dashboard.data_range() == (date(2020, 1, 1), date(2026, 10, 13))
        assert observed.await_count == 3
    finally:
        await dashboard.close()


async def test_real_pdm_adapter_preflight_rejects_observed_uncovered_period(operations, monkeypatch):
    service, graph, _ = operations
    dashboard = DashboardService(graph.settings)
    observed = AsyncMock(return_value=(date(2020, 1, 1), date(2026, 10, 11)))
    monkeypatch.setattr(dashboard.repository, "data_range", observed)
    try:
        graph.catalog = SimpleNamespace(get=lambda _: IndustrialPDMAdapter(dashboard))
        schedule = await service.create_schedule(1, spec())
        run = await service.trigger(schedule["schedule_id"], 1)
        assert run["status"] == "FAILED"
        assert "does not cover" in run["error"]
        assert observed.await_count == 1
        assert graph.calls == []
    finally:
        await dashboard.close()
