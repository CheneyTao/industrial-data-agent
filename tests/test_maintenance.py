from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI

from dataagent.maintenance import KnowledgeMaintenance
from dataagent.models import Agent, AgentKnowledge, BusinessKnowledge


class _RecordingIndexer:
    def __init__(self) -> None:
        self.agent_ids: list[int] = []
        self.business_ids: list[int] = []
        self.removed_agent_ids: list[int] = []

    async def index_agent_knowledge(self, session, knowledge_id: int) -> None:
        self.agent_ids.append(knowledge_id)

    async def index_business_knowledge(self, session, knowledge_id: int) -> None:
        self.business_ids.append(knowledge_id)

    async def remove_agent_knowledge(self, item: AgentKnowledge) -> None:
        self.removed_agent_ids.append(item.id)


class _RecordingStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, location: str) -> None:
        self.deleted.append(location)


async def _seed_maintenance_rows(app: FastAPI):
    now = datetime.now()
    stale = now - timedelta(hours=2)
    async with app.state.database.session_factory() as session:
        agent = Agent(name="Maintenance agent")
        session.add(agent)
        await session.flush()
        rows = {
            "pending": AgentKnowledge(
                agent_id=agent.id,
                title="pending",
                type="QA",
                question="q",
                content="a",
                embedding_status="PENDING",
                updated_time=now,
            ),
            "fresh_processing": AgentKnowledge(
                agent_id=agent.id,
                title="fresh-processing",
                type="QA",
                question="q",
                content="a",
                embedding_status="PROCESSING",
                updated_time=now,
            ),
            "stale_processing": AgentKnowledge(
                agent_id=agent.id,
                title="stale-processing",
                type="QA",
                question="q",
                content="a",
                embedding_status="PROCESSING",
                updated_time=stale,
            ),
            "disabled": AgentKnowledge(
                agent_id=agent.id,
                title="disabled",
                type="QA",
                question="q",
                content="a",
                is_recall=0,
                embedding_status="PENDING",
                updated_time=stale,
            ),
            "old_deleted": AgentKnowledge(
                agent_id=agent.id,
                title="old-deleted",
                type="DOCUMENT",
                is_deleted=1,
                is_resource_cleaned=0,
                file_path="old-resource.txt",
                updated_time=stale,
            ),
            "fresh_deleted": AgentKnowledge(
                agent_id=agent.id,
                title="fresh-deleted",
                type="DOCUMENT",
                is_deleted=1,
                is_resource_cleaned=0,
                file_path="fresh-resource.txt",
                updated_time=now,
            ),
        }
        business_pending = BusinessKnowledge(
            agent_id=agent.id,
            business_term="pending-term",
            embedding_status="PENDING",
            updated_time=now,
        )
        business_stale = BusinessKnowledge(
            agent_id=agent.id,
            business_term="stale-term",
            embedding_status="PROCESSING",
            updated_time=stale,
        )
        session.add_all([*rows.values(), business_pending, business_stale])
        await session.commit()
        return (
            {name: item.id for name, item in rows.items()},
            business_pending.id,
            business_stale.id,
        )


async def test_recovery_retries_pending_and_only_stale_processing_and_cleanup(
    app: FastAPI,
) -> None:
    row_ids, business_pending_id, business_stale_id = await _seed_maintenance_rows(app)
    indexer = _RecordingIndexer()
    storage = _RecordingStorage()
    app.state.knowledge_indexer = indexer
    app.state.file_storage = storage

    await KnowledgeMaintenance(app).recover_once()

    assert indexer.agent_ids == [row_ids["pending"], row_ids["stale_processing"]]
    assert indexer.business_ids == [business_pending_id, business_stale_id]
    assert indexer.removed_agent_ids == [row_ids["old_deleted"]]
    assert storage.deleted == ["old-resource.txt"]
    async with app.state.database.session_factory() as session:
        fresh = await session.get(AgentKnowledge, row_ids["fresh_processing"])
        stale = await session.get(AgentKnowledge, row_ids["stale_processing"])
        disabled = await session.get(AgentKnowledge, row_ids["disabled"])
        old_deleted = await session.get(AgentKnowledge, row_ids["old_deleted"])
        fresh_deleted = await session.get(AgentKnowledge, row_ids["fresh_deleted"])
        assert fresh is not None and fresh.embedding_status == "PROCESSING"
        assert stale is not None and stale.embedding_status == "PENDING"
        assert stale.error_msg == "Recovered stale PROCESSING job"
        assert disabled is not None and disabled.embedding_status == "PENDING"
        assert old_deleted is not None and old_deleted.is_resource_cleaned == 1
        assert fresh_deleted is not None and fresh_deleted.is_resource_cleaned == 0


async def test_recovery_waits_for_database_embedding_model_but_still_cleans_resources(
    app: FastAPI,
) -> None:
    row_ids, _, _ = await _seed_maintenance_rows(app)
    app.state.settings.model_mode = "database"
    app.state.settings.embedding_mode = "database"
    indexer = _RecordingIndexer()
    storage = _RecordingStorage()
    app.state.knowledge_indexer = indexer
    app.state.file_storage = storage

    await KnowledgeMaintenance(app).recover_once()

    assert indexer.agent_ids == []
    assert indexer.business_ids == []
    assert indexer.removed_agent_ids == [row_ids["old_deleted"]]
    assert storage.deleted == ["old-resource.txt"]
