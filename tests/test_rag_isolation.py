from __future__ import annotations

from typing import Any

from dataagent.embeddings import EmbeddingBatch
from dataagent.vector_metadata import TABLE
from dataagent.vectorstore import RetrievalService, SimpleVectorStore


class _StableEmbeddingClient:
    async def embed_bundle(
        self, _session: Any, texts: list[str], *, kind: str
    ) -> EmbeddingBatch:
        assert kind in {"document", "query"}
        return EmbeddingBatch([[1.0, 0.0] for _ in texts])


async def test_schema_replacement_and_search_are_isolated_per_agent(settings) -> None:
    settings.vector_enable_hybrid_search = False
    store = SimpleVectorStore(settings.vector_file_path)
    retrieval = RetrievalService(
        settings,
        llm=_StableEmbeddingClient(),  # type: ignore[arg-type]
        store=store,
    )

    await retrieval.replace(
        None,  # type: ignore[arg-type]
        texts=["agent one old orders schema"],
        metadata={
            "agentId": 1,
            "datasourceId": 99,
            "vectorType": TABLE,
            "tableName": "orders_old",
        },
        old_filter={"agentId": 1, "datasourceId": 99, "vectorType": TABLE},
    )
    await retrieval.replace(
        None,  # type: ignore[arg-type]
        texts=["agent two private payroll schema"],
        metadata={
            "agentId": 2,
            "datasourceId": 99,
            "vectorType": TABLE,
            "tableName": "payroll",
        },
        old_filter={"agentId": 2, "datasourceId": 99, "vectorType": TABLE},
    )
    await retrieval.replace(
        None,  # type: ignore[arg-type]
        texts=["agent one current customers schema"],
        metadata={
            "agentId": 1,
            "datasourceId": 99,
            "vectorType": TABLE,
            "tableName": "customers",
        },
        old_filter={"agentId": 1, "datasourceId": 99, "vectorType": TABLE},
    )

    first = await retrieval.search(
        None,  # type: ignore[arg-type]
        "schema",
        filters={"agentId": 1, "datasourceId": 99, "vectorType": TABLE},
        threshold=-1.0,
    )
    second = await retrieval.search(
        None,  # type: ignore[arg-type]
        "schema",
        filters={"agentId": 2, "datasourceId": 99, "vectorType": TABLE},
        threshold=-1.0,
    )

    assert [(item.metadata["agentId"], item.metadata["tableName"]) for item in first] == [
        (1, "customers")
    ]
    assert [(item.metadata["agentId"], item.metadata["tableName"]) for item in second] == [
        (2, "payroll")
    ]
