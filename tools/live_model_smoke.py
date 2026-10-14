from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dataagent.config import Settings
from dataagent.llm import LLMClient
from dataagent.models import ModelConfig


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


async def smoke(env_file: Path) -> int:
    values = read_env(env_file)
    required = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_ID")
    missing = [key for key in required if not values.get(key)]
    if missing:
        print(json.dumps({"success": False, "missing": missing}))
        return 2

    base_url = values["LLM_BASE_URL"].rstrip("/")
    completions_path = "chat/completions" if base_url.endswith("/v1") else None
    config = ModelConfig(
        provider="env-smoke",
        base_url=base_url,
        api_key=values["LLM_API_KEY"],
        model_name=values["LLM_MODEL_ID"],
        model_type="CHAT",
        completions_path=completions_path,
        max_tokens=16,
    )
    result = await LLMClient(Settings(_env_file=None)).test(None, config)  # type: ignore[arg-type]
    message = str(result.get("message", "")).replace(values["LLM_API_KEY"], "<redacted>")
    print(
        json.dumps(
            {
                "success": bool(result.get("success")),
                "statusCode": result.get("statusCode"),
                "message": message[:300],
                "modelConfigured": True,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.get("success") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Test a Chat model without exposing its credential.")
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(smoke(args.env_file)))


if __name__ == "__main__":
    main()
