from __future__ import annotations

import argparse
import json
from typing import Any
from uuid import uuid4

import httpx


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    event_name: str | None = None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            value = json.loads(line.partition(":")[2].strip())
            value["_sseEvent"] = event_name
            events.append(value)
            event_name = None
    return events


def run(base_url: str, agent_id: int, query: str) -> dict[str, Any]:
    with httpx.Client(base_url=base_url, timeout=300, trust_env=False) as client:
        initialized = client.post(f"/api/agent/{agent_id}/datasources/init")
        initialized.raise_for_status()
        schema = initialized.json()["data"]

        response = client.get(
            "/api/stream/search",
            params={
                "agentId": agent_id,
                "conversationId": f"live-stack-{uuid4().hex}",
                "query": query,
                "humanFeedback": "false",
                "nl2sqlOnly": "true",
            },
        )
        events = _events(response)
        errors = [event for event in events if event.get("error")]
        if errors:
            raise RuntimeError(json.dumps(errors, ensure_ascii=False))
        sql_events = [
            event
            for event in events
            if event.get("nodeName") == "SQL_GENERATE_NODE" and event.get("textType") == "SQL"
        ]
        if not sql_events:
            raise RuntimeError(json.dumps(events, ensure_ascii=False))
        if not events or events[-1].get("_sseEvent") != "complete":
            raise RuntimeError("SSE stream did not finish with a complete event")
        sql = str(sql_events[-1]["text"]).strip()
        if not sql.upper().startswith("SELECT"):
            raise RuntimeError(f"Expected read-only SELECT, received: {sql}")
        return {
            "schema": schema,
            "eventCount": len(events),
            "sql": sql,
            "complete": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify live CHAT + local BGE-M3 + datasource through the running stack"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8065")
    parser.add_argument("--agent-id", type=int, required=True)
    parser.add_argument("--query", default="列出 users 表中的用户")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.agent_id, args.query), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
