from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from dataagent.database import Database
from dataagent.errors import NotFoundError
from dataagent.models import Base

TERMINAL = {"COMPLETED", "PARTIAL", "CANCELLED", "FAILED", "TIMED_OUT"}


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AnalysisTask(Base):
    __tablename__ = "analysis_task"
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    current_node: Mapped[str] = mapped_column(String(100), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AnalysisContext(Base):
    __tablename__ = "bi_analysis_context"
    agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    context_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TaskManager:
    """Durable state for the existing graph, with conditional terminal transitions.

    Cancellation is database-authoritative across HTTP/MCP workers. No second agent/executor lives here.
    """

    def __init__(self, database: Database):
        self.db = database

    async def create(self, task_id: str, agent_id: int, conversation_id: str) -> bool:
        async with self.db.session_factory() as session:
            if await session.get(AnalysisTask, task_id) is None:
                session.add(
                    AnalysisTask(
                        task_id=task_id,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        status="PENDING",
                        updated_at=now(),
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return False
                return True
            return False

    async def get(self, task_id: str, agent_id: int | None = None) -> dict[str, Any]:
        async with self.db.session_factory() as session:
            task = await session.get(AnalysisTask, task_id)
            if task is None or (agent_id is not None and task.agent_id != agent_id):
                raise NotFoundError("Task not found for this agent")
            # A killed/restarted worker cannot leave an unbounded loading state.
            if task.status not in TERMINAL | {"WAITING"} and task.updated_at < now() - timedelta(seconds=90):
                await session.execute(
                    update(AnalysisTask)
                    .where(
                        AnalysisTask.task_id == task_id,
                        AnalysisTask.updated_at == task.updated_at,
                        AnalysisTask.status == task.status,
                    )
                    .values(
                        status="FAILED", error="Worker heartbeat expired; retry the task", updated_at=now()
                    )
                )
                await session.commit()
                await session.refresh(task)
            return {
                "task_id": task.task_id,
                "agent_id": task.agent_id,
                "conversation_id": task.conversation_id,
                "status": task.status,
                "current_node": task.current_node,
                "progress": task.progress,
                "result": json.loads(task.result_json),
                "error": task.error,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
            }

    async def running(self, task_id: str, node: str, progress: int) -> None:
        async with self.db.session_factory() as session:
            await session.execute(
                update(AnalysisTask)
                .where(
                    AnalysisTask.task_id == task_id,
                    AnalysisTask.status.in_(["PENDING", "RUNNING", "WAITING"]),
                )
                .values(status="RUNNING", current_node=node, progress=min(progress, 95), updated_at=now())
            )
            await session.commit()

    async def pulse(self, task_id: str) -> None:
        async with self.db.session_factory() as session:
            await session.execute(
                update(AnalysisTask)
                .where(
                    AnalysisTask.task_id == task_id,
                    AnalysisTask.status.in_(["PENDING", "RUNNING", "CANCELLING"]),
                )
                .values(updated_at=now())
            )
            await session.commit()

    async def cancel(self, task_id: str, agent_id: int | None = None) -> dict[str, Any]:
        await self.get(task_id, agent_id)
        async with self.db.session_factory() as session:
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.task_id == task_id, AnalysisTask.status.in_(["PENDING", "RUNNING"]))
                .values(status="CANCELLING", updated_at=now())
            )
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.task_id == task_id, AnalysisTask.status == "WAITING")
                .values(status="CANCELLED", updated_at=now())
            )
            await session.commit()
        return await self.get(task_id, agent_id)

    async def cancelled(self, task_id: str) -> bool:
        async with self.db.session_factory() as session:
            status = await session.scalar(select(AnalysisTask.status).where(AnalysisTask.task_id == task_id))
            return status in {"CANCELLING", "CANCELLED"}

    async def partial(self, task_id: str, result: dict[str, Any]) -> None:
        async with self.db.session_factory() as session:
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.task_id == task_id, AnalysisTask.status.not_in(TERMINAL))
                .values(result_json=json.dumps(result, ensure_ascii=False, allow_nan=False), updated_at=now())
            )
            await session.commit()

    async def finish(self, task_id: str, status: str, error: str = "") -> None:
        async with self.db.session_factory() as session:
            # Cancellation wins if requested before the successful terminal commit.
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.task_id == task_id, AnalysisTask.status == "CANCELLING")
                .values(status="CANCELLED", updated_at=now())
            )
            await session.execute(
                update(AnalysisTask)
                .where(AnalysisTask.task_id == task_id, AnalysisTask.status.not_in(TERMINAL))
                .values(
                    status=status,
                    error=error[:4000],
                    progress=100 if status == "COMPLETED" else 95,
                    updated_at=now(),
                )
            )
            await session.commit()

    async def context(self, agent_id: int, conversation_id: str) -> dict[str, Any]:
        async with self.db.session_factory() as session:
            row = await session.get(AnalysisContext, (agent_id, conversation_id))
            return json.loads(row.context_json) if row else {}

    async def save_context(self, agent_id: int, conversation_id: str, context: dict[str, Any]) -> None:
        async with self.db.session_factory() as session:
            await session.merge(
                AnalysisContext(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    context_json=json.dumps(context, ensure_ascii=False, allow_nan=False),
                    updated_at=now(),
                )
            )
            await session.commit()
