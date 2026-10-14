from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def run(
    cache_dir: Path,
    device: str,
    extra_site_packages: Path | None,
    model: str,
) -> dict[str, object]:
    if extra_site_packages is not None:
        sys.path.append(str(extra_site_packages))

    from dataagent.config import Settings
    from dataagent.embeddings import LocalBGEM3Embedder

    settings = Settings(
        _env_file=None,
        model_mode="database",
        embedding_mode="local_bge_m3",
        local_embedding_model=model,
        local_embedding_cache_dir=cache_dir,
        local_embedding_device=device,
        local_embedding_use_fp16=True,
        local_embedding_batch_size=1,
    )
    embedder = LocalBGEM3Embedder(settings)
    result = await embedder.encode(["统计 sales 表每月收入"], kind="query")
    assert len(result.dense) == 1 and len(result.dense[0]) == 1024
    assert result.sparse is not None and result.sparse[0]
    return {
        "model": settings.local_embedding_model,
        "device": embedder.device,
        "denseDimension": len(result.dense[0]),
        "sparseNonZero": len(result.sparse[0]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Load and encode once with local BGE-M3")
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--extra-site-packages", type=Path)
    parser.add_argument("--model", default="BAAI/bge-m3")
    args = parser.parse_args()
    result = asyncio.run(run(args.cache_dir, args.device, args.extra_site_packages, args.model))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
