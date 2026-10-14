from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from dataagent.models import AgentKnowledge, BusinessKnowledge

logger = logging.getLogger(__name__)


class KnowledgeMaintenance:
    def __init__(self, app: Any, *, interval_seconds: int | None = None) -> None:
        self.app = app
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else app.state.settings.embedding_recovery_interval_seconds
        )
        self.initial_delay_seconds = app.state.settings.embedding_recovery_initial_delay_seconds
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _run(self) -> None:
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            try:
                await self.recover_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # One external outage must not permanently kill the recovery loop.
                logger.exception("Knowledge maintenance pass failed")
            await asyncio.sleep(self.interval_seconds)

    async def recover_once(self) -> None:
        settings = self.app.state.settings
        processing_before = datetime.now() - timedelta(
            seconds=settings.embedding_processing_stale_seconds
        )
        cleanup_before = datetime.now() - timedelta(seconds=settings.resource_cleanup_stale_seconds)
        async with self.app.state.database.session_factory() as session:
            cleanup = (
                (
                    await session.execute(
                        select(AgentKnowledge).where(
                            AgentKnowledge.is_deleted == 1,
                            AgentKnowledge.is_resource_cleaned == 0,
                            AgentKnowledge.updated_time < cleanup_before,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for item in cleanup:
                await self.app.state.knowledge_indexer.remove_agent_knowledge(item)
                if item.file_path:
                    await self.app.state.file_storage.delete(item.file_path)
                item.is_resource_cleaned = 1
            await session.commit()

            # Database-backed mode should not turn recoverable PENDING rows into
            # FAILED merely because an embedding model has not been configured yet.
            if settings.model_mode != "mock":
                ready = await self.app.state.llm.registry.ready(session)
                if not ready["embeddingModelReady"]:
                    return

            pending_condition = or_(
                AgentKnowledge.embedding_status.is_(None),
                AgentKnowledge.embedding_status == "PENDING",
                and_(
                    AgentKnowledge.embedding_status == "PROCESSING",
                    AgentKnowledge.updated_time < processing_before,
                ),
            )
            agent_pending = (
                (
                    await session.execute(
                        select(AgentKnowledge).where(
                            AgentKnowledge.is_deleted == 0,
                            AgentKnowledge.is_recall == 1,
                            pending_condition,
                        )
                    )
                )
                .scalars()
                .all()
            )
            business_pending_condition = or_(
                BusinessKnowledge.embedding_status.is_(None),
                BusinessKnowledge.embedding_status == "PENDING",
                and_(
                    BusinessKnowledge.embedding_status == "PROCESSING",
                    BusinessKnowledge.updated_time < processing_before,
                ),
            )
            business_pending = (
                (
                    await session.execute(
                        select(BusinessKnowledge).where(
                            BusinessKnowledge.is_deleted == 0,
                            BusinessKnowledge.is_recall == 1,
                            business_pending_condition,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for item in agent_pending:
                if item.embedding_status == "PROCESSING":
                    item.embedding_status = "PENDING"
                    item.error_msg = "Recovered stale PROCESSING job"
                    await session.commit()
                await self.app.state.knowledge_indexer.index_agent_knowledge(session, item.id)
            for item in business_pending:
                if item.embedding_status == "PROCESSING":
                    item.embedding_status = "PENDING"
                    item.error_msg = "Recovered stale PROCESSING job"
                    await session.commit()
                await self.app.state.knowledge_indexer.index_business_knowledge(session, item.id)
            await session.commit()
