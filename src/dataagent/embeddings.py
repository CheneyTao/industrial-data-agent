from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dataagent.config import Settings
from dataagent.errors import ConfigurationError

# The regular Hugging Face downloader is materially more reliable than Xet on
# the verified Windows setup used for this migration.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass(slots=True)
class EmbeddingBatch:
    dense: list[list[float]]
    sparse: list[dict[int, float]] | None = None


class LocalBGEM3Embedder:
    """Lazy, serialized BGE-M3 dense+sparse encoder.

    Model loading and PyTorch inference are blocking and are therefore moved to
    a worker thread.  A single lock protects both lazy initialization and model
    inference because FlagEmbedding's model object is not documented as
    concurrently re-entrant.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any | None = None
        self._device: str | None = None
        self._lock = threading.Lock()

    @property
    def device(self) -> str | None:
        return self._device

    def _resolve_device(self) -> str:
        try:
            import torch
        except ImportError as exc:
            raise ConfigurationError(
                "Local BGE-M3 requires the 'local-rag' extra (PyTorch and FlagEmbedding)"
            ) from exc
        requested = self.settings.local_embedding_device
        cuda_available = bool(torch.cuda.is_available())
        if requested == "cuda" and not cuda_available:
            raise ConfigurationError("Local embedding device is 'cuda', but CUDA is unavailable")
        return "cuda" if requested == "cuda" or (requested == "auto" and cuda_available) else "cpu"

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from FlagEmbedding import BGEM3FlagModel
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import LocalEntryNotFoundError
        except ImportError as exc:
            raise ConfigurationError(
                "Local BGE-M3 requires: pip install -e '.[local-rag]'"
            ) from exc

        self._device = self._resolve_device()
        cache_dir = self.settings.local_embedding_cache_dir.expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        model_name = self.settings.local_embedding_model
        local_model = Path(model_name).expanduser()
        if local_model.exists():
            model_path = str(local_model.resolve())
        else:
            model_path = model_name
            try:
                cached = snapshot_download(
                    repo_id=model_name,
                    cache_dir=str(cache_dir),
                    local_files_only=True,
                )
                model_path = str(Path(cached).resolve())
            except (FileNotFoundError, LocalEntryNotFoundError):
                # FlagEmbedding/Hugging Face performs the normal first-run download.
                pass
        self._model = BGEM3FlagModel(
            model_name_or_path=model_path,
            devices=self._device,
            normalize_embeddings=True,
            use_fp16=self.settings.local_embedding_use_fp16 and self._device == "cuda",
            query_max_length=self.settings.local_embedding_query_max_length,
            passage_max_length=self.settings.local_embedding_passage_max_length,
            cache_dir=str(cache_dir),
        )
        return self._model

    @staticmethod
    def _sparse_rows(weights: Sequence[dict[Any, Any]]) -> list[dict[int, float]]:
        return [
            {int(index): float(value) for index, value in row.items() if float(value) != 0.0}
            for row in weights
        ]

    def _encode_sync(self, texts: Sequence[str], kind: Literal["query", "document"]) -> EmbeddingBatch:
        with self._lock:
            model = self._load()
            max_length = (
                self.settings.local_embedding_query_max_length
                if kind == "query"
                else self.settings.local_embedding_passage_max_length
            )
            output = model.encode(
                sentences=list(texts),
                batch_size=self.settings.local_embedding_batch_size,
                max_length=max_length,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            dense = [[float(value) for value in row] for row in output["dense_vecs"]]
            sparse = self._sparse_rows(output["lexical_weights"])
            return EmbeddingBatch(dense=dense, sparse=sparse)

    async def encode(
        self, texts: Sequence[str], *, kind: Literal["query", "document"]
    ) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch([])
        return await asyncio.to_thread(self._encode_sync, texts, kind)
