from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from dataagent.config import Settings
from dataagent.embeddings import EmbeddingBatch, LocalBGEM3Embedder
from dataagent.llm import LLMClient
from dataagent.vectorstore import MilvusVectorStore, RetrievalService, SimpleVectorStore, VectorDocument


class _FakeBGEM3Model:
    def encode(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["return_dense"] is True
        assert kwargs["return_sparse"] is True
        assert kwargs["return_colbert_vecs"] is False
        return {
            "dense_vecs": [[0.25, 0.75] for _ in kwargs["sentences"]],
            "lexical_weights": [{"10": 0.5, 20: 0.25} for _ in kwargs["sentences"]],
        }


async def test_local_bge_m3_returns_dense_and_sparse_vectors(settings: Settings) -> None:
    settings.embedding_mode = "local_bge_m3"
    embedder = LocalBGEM3Embedder(settings)
    embedder._model = _FakeBGEM3Model()  # type: ignore[assignment]

    result = await embedder.encode(["订单", "收入"], kind="query")

    assert result.dense == [[0.25, 0.75], [0.25, 0.75]]
    assert result.sparse == [{10: 0.5, 20: 0.25}, {10: 0.5, 20: 0.25}]


def test_local_bge_m3_accepts_complete_local_model_directory(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeModel:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    flag_module = ModuleType("FlagEmbedding")
    flag_module.BGEM3FlagModel = FakeModel  # type: ignore[attr-defined]
    hub_module = ModuleType("huggingface_hub")

    def unexpected_download(**_kwargs: Any) -> str:
        raise AssertionError("A local model directory must not trigger a download")

    hub_module.snapshot_download = unexpected_download  # type: ignore[attr-defined]
    hub_errors = ModuleType("huggingface_hub.errors")
    hub_errors.LocalEntryNotFoundError = FileNotFoundError  # type: ignore[attr-defined]
    torch_module = ModuleType("torch")
    torch_module.cuda = SimpleNamespace(is_available=lambda: False)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "FlagEmbedding", flag_module)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub_module)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", hub_errors)
    monkeypatch.setitem(sys.modules, "torch", torch_module)

    settings.local_embedding_model = str(tmp_path)
    settings.local_embedding_device = "auto"
    embedder = LocalBGEM3Embedder(settings)
    embedder._load()

    assert captured["model_name_or_path"] == str(tmp_path.resolve())
    assert captured["devices"] == "cpu"


def test_mock_query_extraction_prefers_explicit_latest_query_tag() -> None:
    prompt = "history: old question\n<latest_query>分析 sales 表月度 revenue</latest_query>\nquery: stale"
    assert LLMClient._mock_latest_query(prompt) == "分析 sales 表月度 revenue"


async def test_llm_uses_explicit_local_embedding_mode(settings: Settings, app) -> None:
    settings.model_mode = "database"
    settings.embedding_mode = "local_bge_m3"
    client = LLMClient(settings)
    called: dict[str, Any] = {}

    async def encode(texts: list[str], *, kind: str) -> EmbeddingBatch:
        called.update(texts=texts, kind=kind)
        return EmbeddingBatch([[1.0, 0.0]], [{7: 0.9}])

    client._local_embedder.encode = encode  # type: ignore[method-assign]
    async with app.state.database.session_factory() as session:
        result = await client.embed_bundle(session, ["查询"], kind="query")

    assert called == {"texts": ["查询"], "kind": "query"}
    assert result.sparse == [{7: 0.9}]


class _BundleLLM:
    async def embed_bundle(self, _session: Any, texts: list[str], *, kind: str) -> EmbeddingBatch:
        assert texts == ["revenue"]
        assert kind == "query"
        return EmbeddingBatch([[1.0, 0.0]], [{3: 0.8}])


class _NativeHybridStore:
    native_hybrid = True

    def __init__(self) -> None:
        self.search_call: dict[str, Any] | None = None
        self.keyword_called = False

    async def search(self, embedding: list[float], **kwargs: Any) -> list[VectorDocument]:
        self.search_call = {"embedding": embedding, **kwargs}
        return [VectorDocument("one", "revenue", score=0.02)]

    async def keyword_search(self, *_args: Any, **_kwargs: Any) -> list[VectorDocument]:
        self.keyword_called = True
        return []


async def test_native_milvus_hybrid_receives_sparse_query_without_double_fusion(
    settings: Settings,
) -> None:
    settings.vector_enable_hybrid_search = True
    store = _NativeHybridStore()
    service = RetrievalService(settings, llm=_BundleLLM(), store=store)  # type: ignore[arg-type]

    result = await service.search(None, "revenue", filters={"agentId": 9})  # type: ignore[arg-type]

    assert [item.id for item in result] == ["one"]
    assert store.search_call == {
        "embedding": [1.0, 0.0],
        "top_k": settings.vector_default_top_k,
        "threshold": settings.vector_default_threshold,
        "filters": {"agentId": 9},
        "sparse_embedding": {3: 0.8},
    }
    assert store.keyword_called is False


def test_milvus_filter_expression_preserves_agent_isolation() -> None:
    assert MilvusVectorStore._expr(
        {"agentId": 3, "vectorType": "schema", "generationNot": "new"}
    ) == (
        'metadata["agentId"] == 3 and metadata["vectorType"] == "schema" and '
        'metadata["generation"] != "new"'
    )


def test_milvus_filter_expression_supports_dynamic_id_membership() -> None:
    assert MilvusVectorStore._expr(
        {"agentId": 3, "vectorType": "agentKnowledge", "agentKnowledgeIdIn": [7, 9]}
    ) == (
        'metadata["agentId"] == 3 and metadata["vectorType"] == "agentKnowledge" and '
        'metadata["agentKnowledgeId"] in [7, 9]'
    )
    assert MilvusVectorStore._expr({"agentKnowledgeIdIn": []}) == (
        'id == "__dataagent_no_match__"'
    )


async def test_embedding_space_identity_is_indexed_and_filters_incompatible_vectors(
    settings: Settings, app
) -> None:
    store = SimpleVectorStore(settings.vector_file_path)
    service = RetrievalService(settings, llm=app.state.llm, store=store)
    async with app.state.database.session_factory() as session:
        space = await app.state.llm.embedding_space_id(session)
        await service.index_texts(session, ["revenue"], {"agentId": 1})
        query_vector = (await app.state.llm.embed_bundle(session, ["revenue"], kind="query")).dense[0]
        await store.add(
            [VectorDocument("legacy-space", "revenue", {"agentId": 1}, query_vector)]
        )
        result = await service.search(
            session,
            "revenue",
            filters={"agentId": 1},
            top_k=10,
            threshold=-1.0,
        )

    assert all(item.id != "legacy-space" for item in result)
    indexed = [item for item in (await store._load()).values() if item.id != "legacy-space"]
    assert indexed and indexed[0].metadata["embeddingSpaceId"] == space


async def test_mock_embedding_space_changes_when_algorithm_dimension_changes(
    settings: Settings, app
) -> None:
    async with app.state.database.session_factory() as session:
        first = await app.state.llm.embedding_space_id(session)
        settings.vector_embedding_dimension += 1
        second = await app.state.llm.embedding_space_id(session)
    assert first != second


def test_mock_embedding_retrieves_mixed_chinese_english_schema() -> None:
    query = LLMClient._hash_embedding("列出 users 表中的用户", 384)
    matching = LLMClient._hash_embedding(
        "table users: id INTEGER 用户ID, username VARCHAR 用户名, email VARCHAR 用户邮箱",
        384,
    )
    unrelated = LLMClient._hash_embedding(
        "table products: price DECIMAL 商品单价, stock INTEGER 商品库存数量",
        384,
    )

    matching_score = sum(left * right for left, right in zip(query, matching, strict=True))
    unrelated_score = sum(left * right for left, right in zip(query, unrelated, strict=True))
    assert matching_score >= 0.2
    assert matching_score > unrelated_score


async def test_replace_keeps_previous_embedding_space_until_cutover(
    settings: Settings, app
) -> None:
    store = SimpleVectorStore(settings.vector_file_path)
    service = RetrievalService(settings, llm=app.state.llm, store=store)
    async with app.state.database.session_factory() as session:
        first_space = await app.state.llm.embedding_space_id(session)
        await service.replace(
            session,
            texts=["old vector"],
            metadata={"agentId": 1, "vectorType": "schema"},
            old_filter={"agentId": 1, "vectorType": "schema"},
        )
        settings.vector_embedding_dimension += 1
        second_space = await app.state.llm.embedding_space_id(session)
        await service.replace(
            session,
            texts=["new vector"],
            metadata={"agentId": 1, "vectorType": "schema"},
            old_filter={"agentId": 1, "vectorType": "schema"},
        )

    documents = list((await store._load()).values())
    assert first_space != second_space
    assert {item.metadata["embeddingSpaceId"] for item in documents} == {
        first_space,
        second_space,
    }


class _HybridFailureStore:
    native_hybrid = False

    def __init__(self, *, fail_vector: bool = False, fail_keyword: bool = False) -> None:
        self.fail_vector = fail_vector
        self.fail_keyword = fail_keyword

    async def search(self, *_args: Any, **_kwargs: Any) -> list[VectorDocument]:
        if self.fail_vector:
            raise RuntimeError("dense unavailable")
        return [VectorDocument("dense", "semantic result")]

    async def keyword_search(self, *_args: Any, **_kwargs: Any) -> list[VectorDocument]:
        if self.fail_keyword:
            raise RuntimeError("keyword unavailable")
        return [VectorDocument("keyword", "keyword result")]


async def test_hybrid_retrieval_requires_dense_branch_but_degrades_keyword_failure(
    settings: Settings,
) -> None:
    settings.vector_enable_hybrid_search = True
    required = RetrievalService(
        settings,
        llm=_BundleLLM(),
        store=_HybridFailureStore(fail_vector=True),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="dense unavailable"):
        await required.search(None, "revenue", filters={})  # type: ignore[arg-type]

    degradable = RetrievalService(
        settings,
        llm=_BundleLLM(),
        store=_HybridFailureStore(fail_keyword=True),  # type: ignore[arg-type]
    )
    result = await degradable.search(None, "revenue", filters={})  # type: ignore[arg-type]
    assert [item.id for item in result] == ["dense"]
