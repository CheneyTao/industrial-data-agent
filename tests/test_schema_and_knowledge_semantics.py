from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from dataagent.documents import KnowledgeIndexer, recalled_evidence
from dataagent.embeddings import EmbeddingBatch
from dataagent.models import (
    Agent,
    AgentDatasource,
    AgentDatasourceTable,
    AgentKnowledge,
    BusinessKnowledge,
    Datasource,
)
from dataagent.vector_metadata import COLUMN, TABLE, VECTOR_TYPE
from dataagent.vectorstore import RetrievalService, SimpleVectorStore


class _StableEmbeddingClient:
    async def embed_bundle(
        self, _session: Any, texts: list[str], *, kind: str
    ) -> EmbeddingBatch:
        assert kind in {"document", "query"}
        return EmbeddingBatch([[1.0, 0.0] for _ in texts])


async def test_schema_index_contains_samples_primary_keys_and_foreign_keys(
    app: FastAPI, tmp_path: Path
) -> None:
    source_path = tmp_path / "schema.db"
    with sqlite3.connect(source_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute(
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, amount REAL, "
            "FOREIGN KEY(parent_id) REFERENCES parent(id))"
        )
        connection.executemany(
            "INSERT INTO parent(id, name) VALUES (?, ?)",
            [(1, "Alpha"), (2, "Beta"), (3, "Alpha")],
        )
        connection.executemany(
            "INSERT INTO child(id, parent_id, amount) VALUES (?, ?, ?)",
            [(1, 1, 10.5), (2, 1, 20.0), (3, 2, 10.5)],
        )

    async with app.state.database.session_factory() as session:
        agent = Agent(name="Schema Agent")
        source = Datasource(
            name="Schema source",
            type="sqlite",
            host="localhost",
            port=0,
            database_name=str(source_path),
            username="",
            password="",
            connection_url=f"sqlite:///{source_path.as_posix()}",
        )
        session.add_all([agent, source])
        await session.flush()
        association = AgentDatasource(
            agent_id=agent.id,
            datasource_id=source.id,
            is_active=1,
        )
        session.add(association)
        await session.flush()
        session.add_all(
            [
                AgentDatasourceTable(agent_datasource_id=association.id, table_name="parent"),
                AgentDatasourceTable(agent_datasource_id=association.id, table_name="child"),
            ]
        )
        await session.commit()

        result = await app.state.schema_indexer.initialize(session, agent.id)

    assert result == {"tableCount": 2, "columnCount": 5}
    documents = (await app.state.vector_store._load()).values()
    table_docs = [item for item in documents if item.metadata[VECTOR_TYPE] == TABLE]
    column_docs = [item for item in documents if item.metadata[VECTOR_TYPE] == COLUMN]
    assert {item.metadata["name"] for item in table_docs} == {"parent", "child"}
    child_table = next(item for item in table_docs if item.metadata["name"] == "child")
    assert child_table.metadata["primaryKey"] == ["id"]
    assert "child.parent_id -> parent.id" in child_table.metadata["foreignKey"]
    parent_name = next(
        item
        for item in column_docs
        if item.metadata["tableName"] == "parent" and item.metadata["name"] == "name"
    )
    assert parent_name.metadata["samples"] == ["Alpha", "Beta"]


async def test_dynamic_recall_filter_uses_current_database_state_without_reembedding(
    app: FastAPI,
) -> None:
    store = SimpleVectorStore(app.state.settings.vector_file_path)
    retrieval = RetrievalService(
        app.state.settings,
        llm=_StableEmbeddingClient(),  # type: ignore[arg-type]
        store=store,
    )
    indexer = KnowledgeIndexer(retrieval, app.state.text_splitter)

    async with app.state.database.session_factory() as session:
        agent = Agent(name="Knowledge Agent")
        session.add(agent)
        await session.flush()
        visible = AgentKnowledge(
            agent_id=agent.id,
            title="Revenue FAQ",
            type="FAQ",
            question="What is revenue?",
            content="Revenue is the recognized sales amount.",
            is_recall=1,
            embedding_status="PENDING",
        )
        hidden = AgentKnowledge(
            agent_id=agent.id,
            title="Private FAQ",
            type="QA",
            question="What is private margin?",
            content="Private answer",
            is_recall=0,
            embedding_status="PENDING",
        )
        business = BusinessKnowledge(
            agent_id=agent.id,
            business_term="GMV",
            description="Gross merchandise value",
            is_recall=1,
            embedding_status="PENDING",
        )
        session.add_all([visible, hidden, business])
        await session.commit()
        await indexer.index_agent_knowledge(session, visible.id)
        await indexer.index_agent_knowledge(session, hidden.id)
        await indexer.index_business_knowledge(session, business.id)
        first = await recalled_evidence(session, retrieval, agent.id, "revenue")

        assert "Revenue FAQ" in first
        assert "Revenue is the recognized sales amount" in first
        assert "Private FAQ" not in first
        assert "GMV" in first
        vector_ids_before = set((await store._load()).keys())

        visible.is_recall = 0
        hidden.is_recall = 1
        business.is_recall = 0
        await session.commit()
        second = await recalled_evidence(session, retrieval, agent.id, "revenue")

        assert "Revenue FAQ" not in second
        assert "Private FAQ" in second
        assert "Private answer" in second
        assert "GMV" not in second
        assert set((await store._load()).keys()) == vector_ids_before

        hidden.is_deleted = 1
        await session.commit()
        assert await recalled_evidence(session, retrieval, agent.id, "revenue") == "无"


async def test_agent_knowledge_create_and_retry_validation_matches_upstream(
    client: httpx.AsyncClient,
) -> None:
    agent = (await client.post("/api/agent", json={"name": "Validation Agent"})).json()
    agent_id = agent["id"]

    missing_file = await client.post(
        "/api/agent-knowledge/create",
        json={"agentId": agent_id, "title": "Doc", "type": "DOCUMENT"},
    )
    assert missing_file.status_code == 400
    assert "File is required" in missing_file.json()["message"]

    missing_question = await client.post(
        "/api/agent-knowledge/create",
        json={"agentId": agent_id, "title": "FAQ", "type": "FAQ", "content": "answer"},
    )
    assert missing_question.status_code == 400
    assert "Question is required" in missing_question.json()["message"]

    unsupported = await client.post(
        "/api/agent-knowledge/create",
        json={"agentId": agent_id, "title": "Unknown", "type": "OTHER"},
    )
    assert unsupported.status_code == 400

    created = await client.post(
        "/api/agent-knowledge/create",
        json={
            "agentId": agent_id,
            "title": "Hidden QA",
            "type": "QA",
            "question": "hidden?",
            "content": "yes",
            "isRecall": 0,
        },
    )
    assert created.status_code == 200
    item_id = created.json()["data"]["id"]
    retry_hidden = await client.post(f"/api/agent-knowledge/retry-embedding/{item_id}")
    assert retry_hidden.status_code == 400
    assert "not recalled" in retry_hidden.json()["message"]
