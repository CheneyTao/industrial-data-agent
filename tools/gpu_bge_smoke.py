from __future__ import annotations

import argparse
import json


def run(model_path: str, query: str) -> dict[str, object]:
    import torch
    from FlagEmbedding import BGEM3FlagModel

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable inside the container")
    model = BGEM3FlagModel(
        model_path,
        devices="cuda",
        normalize_embeddings=True,
        use_fp16=True,
    )
    output = model.encode(
        [query],
        batch_size=1,
        max_length=64,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    return {
        "torch": torch.__version__,
        "cudaRuntime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "denseDimension": len(output["dense_vecs"][0]),
        "sparseNonZero": len(output["lexical_weights"][0]),
        "fp16": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real BGE-M3 embedding on the Docker GPU")
    parser.add_argument("--model", required=True)
    parser.add_argument("--query", default="数据库订单分析")
    args = parser.parse_args()
    print(json.dumps(run(args.model, args.query), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
