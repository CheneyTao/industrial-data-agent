from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def configure(base_url: str, env_file: Path) -> dict[str, Any]:
    values = read_env(env_file)
    required = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_ID")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(f"Missing required environment values: {', '.join(missing)}")

    model_base = values["LLM_BASE_URL"].rstrip("/")
    completions_path = "chat/completions" if model_base.endswith("/v1") else None
    payload: dict[str, Any] = {
        "provider": "chapter4-env",
        "baseUrl": model_base,
        "apiKey": values["LLM_API_KEY"],
        "modelName": values["LLM_MODEL_ID"],
        "temperature": 0.0,
        "isActive": True,
        "maxTokens": 4096,
        "modelType": "CHAT",
        "completionsPath": completions_path,
        "proxyEnabled": False,
    }

    with httpx.Client(base_url=base_url, timeout=180, trust_env=False) as client:
        listed = client.get("/api/model-config/list")
        listed.raise_for_status()
        models = listed.json().get("data") or []
        existing = next(
            (
                model
                for model in models
                if model.get("provider") == payload["provider"]
                and model.get("modelType") == "CHAT"
            ),
            None,
        )
        if existing is None:
            saved = client.post("/api/model-config/add", json=payload)
        else:
            payload["id"] = int(existing["id"])
            saved = client.put("/api/model-config/update", json=payload)
        saved.raise_for_status()
        if not saved.json().get("success"):
            raise RuntimeError(str(saved.json().get("message") or "Model configuration failed"))

        refreshed = client.get("/api/model-config/list")
        refreshed.raise_for_status()
        configured = next(
            model
            for model in (refreshed.json().get("data") or [])
            if model.get("provider") == payload["provider"] and model.get("modelType") == "CHAT"
        )
        model_id = int(configured["id"])
        tested = client.post(f"/api/model-config/test/{model_id}")
        tested.raise_for_status()
        test_body = tested.json()
        ready = client.get("/api/model-config/check-ready")
        ready.raise_for_status()
        ready_body = ready.json().get("data") or {}

    message = str(test_body.get("message") or "").replace(values["LLM_API_KEY"], "<redacted>")
    return {
        "configured": True,
        "modelId": model_id,
        "modelName": values["LLM_MODEL_ID"],
        "active": bool(configured.get("isActive")),
        "apiKey": configured.get("apiKey"),
        "connectionTest": bool(test_body.get("success")),
        "connectionMessage": message,
        "ready": ready_body,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure and verify DataAgent CHAT from an authorized .env without printing secrets"
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8065")
    args = parser.parse_args()
    result = configure(args.base_url, args.env_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["connectionTest"] and result["ready"].get("ready") else 1)


if __name__ == "__main__":
    main()
