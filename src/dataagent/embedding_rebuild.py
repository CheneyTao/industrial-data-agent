from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.documents import KnowledgeIndexer, TextSplitter
from dataagent.errors import DataAgentError
from dataagent.models import AgentDatasource, AgentKnowledge, BusinessKnowledge, ModelConfig
from dataagent.schema_index import SchemaIndexer
from dataagent.vectorstore import RetrievalService

if TYPE_CHECKING:
    from dataagent.embeddings import EmbeddingBatch
    from dataagent.llm import LLMClient


class FixedEmbeddingClient:
    """Embedding-only adapter pinned to one inactive model configuration."""

    def __init__(self, client: LLMClient, config: ModelConfig) -> None:
        self.client = client
        self.config = config

    async def embedding_space_id(self, _session: AsyncSession) -> str:
        return self.client.embedding_space_id_for_config(self.config)

    async def embed_bundle(
        self,
        _session: AsyncSession,
        texts: list[str],
        *,
        kind: Literal["query", "document"] = "document",
    ) -> EmbeddingBatch:
        return await self.client.embed_bundle_for_config(self.config, texts, kind=kind)

    async def embed(
        self,
        session: AsyncSession,
        texts: list[str],
        *,
        kind: Literal["query", "document"] = "document",
    ) -> list[list[float]]:
        return (await self.embed_bundle(session, texts, kind=kind)).dense


class EmbeddingRebuildService:
    """Build a complete candidate vector generation before model cut-over.

    Existing vectors stay queryable because ``RetrievalService.replace`` scopes
    deletion to the candidate embedding-space ID.  Only after every active
    schema and recalled knowledge item succeeds may the caller switch the
    database's active embedding model.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.lock = asyncio.Lock()

    async def rebuild(self, session: AsyncSession, config: ModelConfig) -> dict[str, int]:
        async with self.lock:
            adapter = FixedEmbeddingClient(self.app.state.llm, config)
            retrieval = RetrievalService(
                self.app.state.settings,
                adapter,  # type: ignore[arg-type]
                self.app.state.vector_store,
            )
            schema_indexer = SchemaIndexer(self.app.state.connectors, retrieval)
            splitter = TextSplitter(self.app.state.settings, adapter)  # type: ignore[arg-type]
            knowledge_indexer = KnowledgeIndexer(retrieval, splitter)

            agent_ids = (
                await session.execute(
                    select(AgentDatasource.agent_id)
                    .where(AgentDatasource.is_active == 1)
                    .distinct()
                    .order_by(AgentDatasource.agent_id)
                )
            ).scalars().all()
            schema_tables = 0
            schema_columns = 0
            for agent_id in agent_ids:
                result = await schema_indexer.initialize(session, int(agent_id))
                schema_tables += result["tableCount"]
                schema_columns += result["columnCount"]

            agent_items = (
                await session.execute(
                    select(AgentKnowledge)
                    .where(
                        AgentKnowledge.is_deleted == 0,
                        AgentKnowledge.is_recall == 1,
                    )
                    .order_by(AgentKnowledge.id)
                )
            ).scalars().all()
            business_items = (
                await session.execute(
                    select(BusinessKnowledge)
                    .where(
                        BusinessKnowledge.is_deleted == 0,
                        BusinessKnowledge.is_recall == 1,
                    )
                    .order_by(BusinessKnowledge.id)
                )
            ).scalars().all()

            for item in agent_items:
                previous_status, previous_error = item.embedding_status, item.error_msg
                await knowledge_indexer.index_agent_knowledge(session, item.id)
                await session.refresh(item)
                if item.embedding_status != "COMPLETED":
                    error = item.error_msg or "unknown embedding error"
                    item.embedding_status, item.error_msg = previous_status, previous_error
                    await session.commit()
                    raise DataAgentError(
                        f"Candidate embedding rebuild failed for AgentKnowledge {item.id}: {error}",
                        status_code=502,
                    )

            for item in business_items:
                previous_status, previous_error = item.embedding_status, item.error_msg
                await knowledge_indexer.index_business_knowledge(session, item.id)
                await session.refresh(item)
                if item.embedding_status != "COMPLETED":
                    error = item.error_msg or "unknown embedding error"
                    item.embedding_status, item.error_msg = previous_status, previous_error
                    await session.commit()
                    raise DataAgentError(
                        f"Candidate embedding rebuild failed for BusinessKnowledge {item.id}: {error}",
                        status_code=502,
                    )

            return {
                "agents": len(agent_ids),
                "schemaTables": schema_tables,
                "schemaColumns": schema_columns,
                "agentKnowledge": len(agent_items),
                "businessKnowledge": len(business_items),
            }
