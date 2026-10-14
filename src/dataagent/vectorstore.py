from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.config import Settings, get_settings
from dataagent.errors import ConfigurationError
from dataagent.llm import LLMClient, get_llm_client
from dataagent.vector_metadata import EMBEDDING_SPACE_ID


@dataclass(slots=True)
class VectorDocument:
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    score: float = 0.0
    sparse_embedding: dict[int, float] | None = None


class VectorStore(Protocol):
    async def add(self, documents: list[VectorDocument]) -> None: ...

    async def delete(self, ids: list[str]) -> None: ...

    async def delete_by_metadata(self, filters: dict[str, Any]) -> int: ...

    async def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        threshold: float,
        filters: dict[str, Any],
        sparse_embedding: dict[int, float] | None = None,
    ) -> list[VectorDocument]: ...

    async def keyword_search(
        self, query: str, *, top_k: int, min_score: float, filters: dict[str, Any]
    ) -> list[VectorDocument]: ...


def _matches(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, value in filters.items():
        if key.endswith("Not"):
            if str(metadata.get(key[:-3])) == str(value):
                return False
        elif key.endswith("In"):
            allowed = {str(item) for item in value}
            if str(metadata.get(key[:-2])) not in allowed:
                return False
        elif str(metadata.get(key)) != str(value):
            return False
    return True


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    denom = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right, strict=True)) / denom if denom else 0.0


class SimpleVectorStore:
    """Metadata-aware JSON vector store matching the upstream zero-setup backend."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        # The HTTP API and MCP server are separate processes in Compose.  An
        # asyncio.Lock only protects one process, so mutations also need an OS
        # file lock to prevent lost updates to the shared atomic JSON snapshot.
        # asyncio.to_thread may acquire and release on different worker threads;
        # disable FileLock's thread-local recursion counter for that valid use.
        self._file_lock = FileLock(str(path) + ".lock", timeout=30, thread_local=False)
        self._documents: dict[str, VectorDocument] | None = None
        self._source_stamp: tuple[int, int] | None = None

    async def _acquire_file_lock(self) -> None:
        try:
            await asyncio.to_thread(self._file_lock.acquire)
        except FileLockTimeout as exc:
            raise ConfigurationError(f"Timed out locking vector store: {self.path}") from exc

    async def _release_file_lock(self) -> None:
        await asyncio.to_thread(self._file_lock.release)

    async def _file_stamp(self) -> tuple[int, int] | None:
        try:
            stat = await asyncio.to_thread(self.path.stat)
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    async def _load(self) -> dict[str, VectorDocument]:
        stamp = await self._file_stamp()
        if self._documents is not None and stamp == self._source_stamp:
            return self._documents
        if stamp is None:
            self._documents = {}
            self._source_stamp = None
            return self._documents
        try:
            payload = json.loads(await asyncio.to_thread(self.path.read_text, encoding="utf-8"))
            items = payload.get("documents", payload) if isinstance(payload, dict) else []
            if isinstance(items, dict):
                values = items.values()
            else:
                values = items
            self._documents = {
                item["id"]: VectorDocument(
                    id=item["id"],
                    content=item["content"],
                    metadata=item.get("metadata", {}),
                    embedding=item.get("embedding"),
                    sparse_embedding={
                        int(key): float(value)
                        for key, value in (item.get("sparse_embedding") or {}).items()
                    }
                    or None,
                )
                for item in values
            }
            self._source_stamp = stamp
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ConfigurationError(f"Vector store file is corrupt: {self.path}: {exc}") from exc
        return self._documents

    async def _persist(self) -> None:
        assert self._documents is not None
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "documents": [asdict(document) for document in self._documents.values()],
        }
        handle, temp_name = await asyncio.to_thread(
            tempfile.mkstemp,
            prefix=".vectorstore-",
            suffix=".json",
            dir=self.path.parent,
        )
        await asyncio.to_thread(os.close, handle)
        temp_path = Path(temp_name)
        try:
            await asyncio.to_thread(
                temp_path.write_text,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            await asyncio.to_thread(os.replace, temp_path, self.path)
            self._source_stamp = await self._file_stamp()
        finally:
            await asyncio.to_thread(temp_path.unlink, missing_ok=True)

    async def add(self, documents: list[VectorDocument]) -> None:
        async with self._lock:
            await self._acquire_file_lock()
            try:
                self._documents = None
                current = await self._load()
                for document in documents:
                    current[document.id] = document
                await self._persist()
            finally:
                await self._release_file_lock()

    async def delete(self, ids: list[str]) -> None:
        async with self._lock:
            await self._acquire_file_lock()
            try:
                self._documents = None
                current = await self._load()
                for document_id in ids:
                    current.pop(document_id, None)
                await self._persist()
            finally:
                await self._release_file_lock()

    async def delete_by_metadata(self, filters: dict[str, Any]) -> int:
        async with self._lock:
            await self._acquire_file_lock()
            try:
                self._documents = None
                current = await self._load()
                ids = [key for key, value in current.items() if _matches(value.metadata, filters)]
                for document_id in ids:
                    current.pop(document_id, None)
                if ids:
                    await self._persist()
                return len(ids)
            finally:
                await self._release_file_lock()

    async def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        threshold: float,
        filters: dict[str, Any],
        sparse_embedding: dict[int, float] | None = None,
    ) -> list[VectorDocument]:
        current = await self._load()
        matches = []
        for document in current.values():
            if document.embedding is None or not _matches(document.metadata, filters):
                continue
            score = _cosine(embedding, document.embedding)
            if score >= threshold:
                matches.append(
                    VectorDocument(document.id, document.content, dict(document.metadata), None, score)
                )
        return sorted(matches, key=lambda item: item.score, reverse=True)[:top_k]

    async def keyword_search(
        self, query: str, *, top_k: int, min_score: float, filters: dict[str, Any]
    ) -> list[VectorDocument]:
        terms = {term.lower() for term in query.split() if term.strip()}
        current = await self._load()
        matches = []
        for document in current.values():
            if not _matches(document.metadata, filters):
                continue
            lowered = document.content.lower()
            hit_count = sum(1 for term in terms if term in lowered)
            score = hit_count / max(len(terms), 1)
            if score >= min_score:
                matches.append(
                    VectorDocument(document.id, document.content, dict(document.metadata), None, score)
                )
        return sorted(matches, key=lambda item: item.score, reverse=True)[:top_k]


class ElasticsearchVectorStore:
    def __init__(self, settings: Settings) -> None:
        try:
            from elasticsearch import AsyncElasticsearch
        except ImportError as exc:
            raise ConfigurationError("Install the 'vector' extra to use Elasticsearch") from exc
        self.client = AsyncElasticsearch(settings.elasticsearch_url)
        self.index = settings.elasticsearch_index

    async def _ensure_index(self, dimension: int) -> None:
        if await self.client.indices.exists(index=self.index):
            mapping = await self.client.indices.get_mapping(index=self.index)
            configured = (
                mapping.get(self.index, {})
                .get("mappings", {})
                .get("properties", {})
                .get("embedding", {})
                .get("dims")
            )
            if configured is not None and int(configured) != dimension:
                raise ConfigurationError(
                    f"Elasticsearch index '{self.index}' has dimension {configured}, "
                    f"but the active embedding model returned {dimension}. Use a new versioned index."
                )
        else:
            await self.client.indices.create(
                index=self.index,
                mappings={
                    "properties": {
                        "content": {"type": "text"},
                        "metadata": {"type": "object"},
                        "metadata_filter": {"type": "flattened"},
                        "embedding": {"type": "dense_vector", "dims": dimension, "index": True},
                    }
                },
            )

    async def add(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return
        dimension = len(documents[0].embedding or [])
        await self._ensure_index(dimension)
        operations: list[dict[str, Any]] = []
        for document in documents:
            operations.extend(
                [
                    {"index": {"_index": self.index, "_id": document.id}},
                    {
                        "content": document.content,
                        "metadata": document.metadata,
                        "metadata_filter": {key: str(value) for key, value in document.metadata.items()},
                        "embedding": document.embedding,
                    },
                ]
            )
        response = await self.client.bulk(operations=operations, refresh=True)
        if response.get("errors"):
            failure = next(
                (
                    action
                    for item in response.get("items", [])
                    for action in item.values()
                    if action.get("error")
                ),
                {},
            )
            raise ConfigurationError(f"Elasticsearch bulk indexing failed: {failure.get('error')}")

    async def delete(self, ids: list[str]) -> None:
        if ids and await self.client.indices.exists(index=self.index):
            await self.client.delete_by_query(
                index=self.index, query={"ids": {"values": ids}}, refresh=True, conflicts="proceed"
            )

    @staticmethod
    def _filter(filters: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for key, value in filters.items():
            if key.endswith("Not"):
                continue
            if key.endswith("In"):
                result.append(
                    {"terms": {f"metadata_filter.{key[:-2]}": [str(item) for item in value]}}
                )
            else:
                result.append({"term": {f"metadata_filter.{key}": str(value)}})
        return result

    @staticmethod
    def _must_not(filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"term": {f"metadata_filter.{key[:-3]}": str(value)}}
            for key, value in filters.items()
            if key.endswith("Not")
        ]

    async def delete_by_metadata(self, filters: dict[str, Any]) -> int:
        if not await self.client.indices.exists(index=self.index):
            return 0
        response = await self.client.delete_by_query(
            index=self.index,
            query={
                "bool": {
                    "filter": self._filter(filters),
                    "must_not": self._must_not(filters),
                }
            },
            refresh=True,
            conflicts="proceed",
        )
        return int(response.get("deleted", 0))

    @staticmethod
    def _from_hit(hit: dict[str, Any]) -> VectorDocument:
        source = hit["_source"]
        return VectorDocument(hit["_id"], source["content"], source.get("metadata", {}), score=hit["_score"])

    async def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        threshold: float,
        filters: dict[str, Any],
        sparse_embedding: dict[int, float] | None = None,
    ) -> list[VectorDocument]:
        if not await self.client.indices.exists(index=self.index):
            return []
        response = await self.client.search(
            index=self.index,
            size=top_k,
            min_score=threshold + 1.0,
            query={
                "script_score": {
                    "query": {
                        "bool": {
                            "filter": self._filter(filters),
                            "must_not": self._must_not(filters),
                        }
                    },
                    "script": {
                        "source": "cosineSimilarity(params.q, 'embedding') + 1.0",
                        "params": {"q": embedding},
                    },
                }
            },
        )
        documents = [self._from_hit(hit) for hit in response["hits"]["hits"]]
        for document in documents:
            document.score -= 1.0
        return documents

    async def keyword_search(
        self, query: str, *, top_k: int, min_score: float, filters: dict[str, Any]
    ) -> list[VectorDocument]:
        if not await self.client.indices.exists(index=self.index):
            return []
        response = await self.client.search(
            index=self.index,
            size=top_k,
            min_score=min_score,
            query={
                "bool": {
                    "must": [{"match": {"content": query}}],
                    "filter": self._filter(filters),
                    "must_not": self._must_not(filters),
                }
            },
        )
        return [self._from_hit(hit) for hit in response["hits"]["hits"]]

    async def close(self) -> None:
        await self.client.close()


class MilvusVectorStore:
    def __init__(self, settings: Settings) -> None:
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise ConfigurationError("Install the 'vector' extra to use Milvus") from exc
        self.client = MilvusClient(uri=settings.milvus_uri)
        self.collection = settings.milvus_collection
        self.hybrid = (
            settings.embedding_mode == "local_bge_m3" and settings.vector_enable_hybrid_search
        )
        self.native_hybrid = self.hybrid
        self.rrf_k = settings.milvus_hybrid_rrf_k
        self.candidate_multiplier = max(1, settings.milvus_hybrid_candidate_multiplier)
        self.delete_limit = settings.vector_batch_delete_limit

    def _ensure_collection(self, dimension: int) -> None:
        from pymilvus import DataType, MilvusClient

        if self.client.has_collection(self.collection):
            description = self.client.describe_collection(collection_name=self.collection)
            fields = {field.get("name") for field in description.get("fields", [])}
            required = {"id", "embedding", "content", "metadata"}
            if self.hybrid:
                required.add("sparse_embedding")
            missing = sorted(required - fields)
            if missing:
                raise ConfigurationError(
                    f"Milvus collection '{self.collection}' has an incompatible vector schema; "
                    f"missing {missing}. Select a new versioned collection and rebuild the index."
                )
            embedding_field = next(
                (field for field in description.get("fields", []) if field.get("name") == "embedding"),
                {},
            )
            configured_dimension = (embedding_field.get("params") or {}).get("dim") or embedding_field.get(
                "dim"
            )
            if configured_dimension is not None and int(configured_dimension) != dimension:
                raise ConfigurationError(
                    f"Milvus collection '{self.collection}' has dimension {configured_dimension}, "
                    f"but the active embedding model returned {dimension}. Use a new versioned collection."
                )
            return

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=dimension)
        if self.hybrid:
            schema.add_field(field_name="sparse_embedding", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="metadata", datatype=DataType.JSON)

        indexes = MilvusClient.prepare_index_params()
        indexes.add_index(
            field_name="embedding",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 256},
        )
        if self.hybrid:
            indexes.add_index(
                field_name="sparse_embedding",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="IP",
                params={"inverted_index_algo": "DAAT_MAXSCORE"},
            )
        self.client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=indexes,
        )

    async def add(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return
        await asyncio.to_thread(self._ensure_collection, len(documents[0].embedding or []))
        data = [
            {
                "id": document.id,
                "embedding": document.embedding,
                "content": document.content,
                "metadata": document.metadata,
                **(
                    {"sparse_embedding": document.sparse_embedding}
                    if self.hybrid
                    else {}
                ),
            }
            for document in documents
        ]
        if self.hybrid and any(not document.sparse_embedding for document in documents):
            raise ConfigurationError("Milvus hybrid mode requires sparse BGE-M3 document vectors")
        await asyncio.to_thread(self.client.upsert, self.collection, data=data)

    async def delete(self, ids: list[str]) -> None:
        if ids and await asyncio.to_thread(self.client.has_collection, self.collection):
            await asyncio.to_thread(self.client.delete, self.collection, ids=ids)

    @staticmethod
    def _expr(filters: dict[str, Any]) -> str:
        clauses: list[str] = []
        for key, value in filters.items():
            negative = key.endswith("Not")
            membership = key.endswith("In")
            actual_key = key[:-3] if negative else key[:-2] if membership else key
            if membership:
                values = list(value)
                if not values:
                    return 'id == "__dataagent_no_match__"'
                clauses.append(
                    f"metadata[{json.dumps(actual_key, ensure_ascii=False)}] in "
                    f"{json.dumps(values, ensure_ascii=False)}"
                )
                continue
            operator = "!=" if negative else "=="
            clauses.append(
                f"metadata[{json.dumps(actual_key, ensure_ascii=False)}] {operator} "
                f"{json.dumps(value, ensure_ascii=False)}"
            )
        return " and ".join(clauses) if clauses else 'id != ""'

    async def delete_by_metadata(self, filters: dict[str, Any]) -> int:
        if not await asyncio.to_thread(self.client.has_collection, self.collection):
            return 0
        deleted = 0
        while True:
            rows = await asyncio.to_thread(
                self.client.query,
                self.collection,
                filter=self._expr(filters),
                output_fields=["id"],
                limit=self.delete_limit,
            )
            ids = [str(row["id"]) for row in rows]
            if not ids:
                return deleted
            await self.delete(ids)
            deleted += len(ids)
            if len(ids) < self.delete_limit:
                return deleted

    async def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        threshold: float,
        filters: dict[str, Any],
        sparse_embedding: dict[int, float] | None = None,
    ) -> list[VectorDocument]:
        if not await asyncio.to_thread(self.client.has_collection, self.collection):
            return []
        candidate_limit = max(top_k, top_k * self.candidate_multiplier)
        expression = self._expr(filters)
        if self.hybrid:
            if not sparse_embedding:
                raise ConfigurationError("Milvus hybrid search requires a sparse BGE-M3 query vector")
            from pymilvus import AnnSearchRequest, RRFRanker

            dense_request = AnnSearchRequest(
                [embedding],
                "embedding",
                {"metric_type": "COSINE", "params": {"ef": 128}},
                limit=candidate_limit,
                expr=expression,
            )
            sparse_request = AnnSearchRequest(
                [sparse_embedding],
                "sparse_embedding",
                {"metric_type": "IP", "params": {"drop_ratio_search": 0.0}},
                limit=candidate_limit,
                expr=expression,
            )
            rows = await asyncio.to_thread(
                self.client.hybrid_search,
                collection_name=self.collection,
                reqs=[dense_request, sparse_request],
                ranker=RRFRanker(k=self.rrf_k),
                limit=top_k,
                output_fields=["content", "metadata"],
            )
        else:
            rows = await asyncio.to_thread(
                self.client.search,
                self.collection,
                data=[embedding],
                filter=expression,
                limit=top_k,
                output_fields=["content", "metadata"],
                search_params={"metric_type": "COSINE", "params": {"ef": 128}},
            )
        documents: list[VectorDocument] = []
        for hit in rows[0] if rows else []:
            entity = hit.get("entity", {})
            metadata = entity.get("metadata") or {}
            score = float(hit.get("distance", hit.get("score", 0.0)))
            # RRF scores and cosine scores are not in the same numeric space.  Milvus
            # already ranks the two BGE-M3 branches in hybrid mode, so the upstream
            # cosine threshold only applies to the dense-only path.
            if _matches(metadata, filters) and (self.hybrid or score >= threshold):
                documents.append(
                    VectorDocument(str(hit["id"]), entity.get("content", ""), metadata, score=score)
                )
        return documents[:top_k]

    async def keyword_search(
        self, query: str, *, top_k: int, min_score: float, filters: dict[str, Any]
    ) -> list[VectorDocument]:
        # BGE-M3 sparse retrieval is fused inside ``search``.  Dense-only Milvus has
        # no portable full-text capability across supported server versions.
        return []

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)


class RetrievalService:
    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        store: VectorStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or get_llm_client()
        self.store = store or create_vector_store(self.settings)

    async def index_texts(
        self,
        session: AsyncSession,
        texts: list[str],
        metadata: dict[str, Any] | list[dict[str, Any]],
    ) -> list[str]:
        if not texts:
            return []
        embedding_space = await self._embedding_space_id(session)
        vectors: list[list[float]] = []
        sparse_vectors: list[dict[int, float]] | None = []
        for batch in self._embedding_batches(texts):
            embedded = await self.llm.embed_bundle(session, batch, kind="document")
            vectors.extend(embedded.dense)
            if embedded.sparse is None:
                sparse_vectors = None
            elif sparse_vectors is not None:
                sparse_vectors.extend(embedded.sparse)
        metadata_items = metadata if isinstance(metadata, list) else [metadata] * len(texts)
        if embedding_space is not None:
            metadata_items = [
                {**item, EMBEDDING_SPACE_ID: embedding_space} for item in metadata_items
            ]
        documents = [
            VectorDocument(
                str(uuid4()),
                text,
                dict(meta),
                vector,
                sparse_embedding=sparse_vectors[index] if sparse_vectors is not None else None,
            )
            for index, (text, meta, vector) in enumerate(
                zip(texts, metadata_items, vectors, strict=True)
            )
        ]
        await self.store.add(documents)
        return [document.id for document in documents]

    async def _embedding_space_id(self, session: AsyncSession) -> str | None:
        resolver = getattr(self.llm, "embedding_space_id", None)
        if resolver is None:
            # Small protocol fakes and third-party embedding adapters can opt out;
            # the built-in client always supplies an identity.
            return None
        return str(await resolver(session))

    async def _space_filters(
        self, session: AsyncSession, filters: dict[str, Any]
    ) -> dict[str, Any]:
        embedding_space = await self._embedding_space_id(session)
        if embedding_space is None:
            return dict(filters)
        return {**filters, EMBEDDING_SPACE_ID: embedding_space}

    def _embedding_batches(self, texts: list[str]) -> list[list[str]]:
        """Apply the upstream token budget and item cap to embedding requests."""

        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")

            def token_count(value: str) -> int:
                return len(encoding.encode(value))

        except Exception:

            def token_count(value: str) -> int:
                # Conservative fallback for mixed Chinese/English text.
                return max(1, len(value))

        token_limit = max(
            1,
            int(
                self.settings.embedding_batch_max_tokens * (1.0 - self.settings.embedding_batch_reserve_ratio)
            ),
        )
        item_limit = max(1, self.settings.embedding_batch_max_items)
        batches: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0
        for value in texts:
            value_tokens = token_count(value)
            if current and (len(current) >= item_limit or current_tokens + value_tokens > token_limit):
                batches.append(current)
                current = []
                current_tokens = 0
            current.append(value)
            current_tokens += value_tokens
        if current:
            batches.append(current)
        return batches

    async def replace(
        self,
        session: AsyncSession,
        *,
        texts: list[str],
        metadata: dict[str, Any] | list[dict[str, Any]],
        old_filter: dict[str, Any],
    ) -> list[str]:
        # Upstream consistency semantics: add the new vectors first, then remove the old
        # generation. If deletion fails, roll the new generation back.
        generation = str(uuid4())
        embedding_space = await self._embedding_space_id(session)
        scoped_old_filter = dict(old_filter)
        if embedding_space is not None:
            # A rebuild for a candidate model must never remove the currently
            # active model's generation before traffic switches.  Dimensions
            # alone do not identify a vector space, so replacement and search
            # use the same embedding-space boundary.
            scoped_old_filter[EMBEDDING_SPACE_ID] = embedding_space
        metas = metadata if isinstance(metadata, list) else [metadata] * len(texts)
        enriched = [{**item, "generation": generation} for item in metas]
        ids = await self.index_texts(session, texts, enriched)
        try:
            await self.store.delete_by_metadata({**scoped_old_filter, "generationNot": generation})
        except Exception:
            await self.store.delete(ids)
            raise
        return ids

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        filters: dict[str, Any],
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[VectorDocument]:
        actual_top_k = top_k or self.settings.vector_default_top_k
        actual_threshold = self.settings.vector_default_threshold if threshold is None else threshold
        actual_filters = await self._space_filters(session, filters)
        embedded = await self.llm.embed_bundle(session, [query], kind="query")
        embedding = embedded.dense[0]
        sparse_embedding = embedded.sparse[0] if embedded.sparse else None
        return await self._search_embedded(
            query,
            embedding,
            sparse_embedding,
            filters=actual_filters,
            top_k=actual_top_k,
            threshold=actual_threshold,
        )

    async def search_many(
        self,
        session: AsyncSession,
        query: str,
        *,
        filter_sets: list[dict[str, Any]],
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[list[VectorDocument] | BaseException]:
        """Embed once, then search independent evidence branches concurrently.

        This preserves the Java parallel-retrieval behaviour without concurrently
        using one SQLAlchemy AsyncSession to resolve the active embedding model.
        """

        if not filter_sets:
            return []
        actual_top_k = top_k or self.settings.vector_default_top_k
        actual_threshold = self.settings.vector_default_threshold if threshold is None else threshold
        try:
            embedding_space = await self._embedding_space_id(session)
            actual_filter_sets = [
                ({**filters, EMBEDDING_SPACE_ID: embedding_space} if embedding_space else dict(filters))
                for filters in filter_sets
            ]
            embedded = await self.llm.embed_bundle(session, [query], kind="query")
            embedding = embedded.dense[0]
            sparse_embedding = embedded.sparse[0] if embedded.sparse else None
        except BaseException as exc:
            return [exc for _ in filter_sets]
        return await asyncio.gather(
            *(
                self._search_embedded(
                    query,
                    embedding,
                    sparse_embedding,
                    filters=filters,
                    top_k=actual_top_k,
                    threshold=actual_threshold,
                )
                for filters in actual_filter_sets
            ),
            return_exceptions=True,
        )

    async def _search_embedded(
        self,
        query: str,
        embedding: list[float],
        sparse_embedding: dict[int, float] | None,
        *,
        filters: dict[str, Any],
        top_k: int,
        threshold: float,
    ) -> list[VectorDocument]:
        vector_task = asyncio.create_task(
            self.store.search(
                embedding,
                top_k=top_k,
                threshold=threshold,
                filters=filters,
                sparse_embedding=sparse_embedding,
            )
        )
        if not self.settings.vector_enable_hybrid_search or getattr(
            self.store, "native_hybrid", False
        ):
            return await vector_task
        keyword_task = asyncio.create_task(
            self.store.keyword_search(
                query,
                top_k=top_k * 2,
                min_score=self.settings.elasticsearch_min_score,
                filters=filters,
            )
        )
        timeout = self.settings.vector_hybrid_timeout_ms / 1000
        vector, keyword = await asyncio.gather(
            asyncio.wait_for(vector_task, timeout),
            asyncio.wait_for(keyword_task, timeout),
            return_exceptions=True,
        )
        if isinstance(vector, BaseException):
            # The dense branch is the semantic retrieval source of truth.  Only
            # keyword failure may degrade; hiding a dense outage returns plausible
            # but incorrect context and differs from the upstream contract.
            raise vector
        vector_results = vector
        keyword_results = [] if isinstance(keyword, BaseException) else keyword
        return reciprocal_rank_fusion(vector_results, keyword_results, top_k=top_k)


def reciprocal_rank_fusion(
    vector: list[VectorDocument], keyword: list[VectorDocument], *, top_k: int, k: int = 60
) -> list[VectorDocument]:
    documents: dict[str, VectorDocument] = {}
    scores: dict[str, float] = {}
    for ranking in (vector, keyword):
        for rank, document in enumerate(ranking, start=1):
            documents[document.id] = document
            scores[document.id] = scores.get(document.id, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores, key=scores.get, reverse=True)[:top_k]  # type: ignore[arg-type]
    result = []
    for document_id in ordered:
        document = documents[document_id]
        result.append(
            VectorDocument(document.id, document.content, document.metadata, score=scores[document_id])
        )
    return result


def create_vector_store(settings: Settings | None = None) -> VectorStore:
    config = settings or get_settings()
    if config.vector_store_type == "elasticsearch":
        return ElasticsearchVectorStore(config)
    if config.vector_store_type == "milvus":
        return MilvusVectorStore(config)
    return SimpleVectorStore(config.vector_file_path)
