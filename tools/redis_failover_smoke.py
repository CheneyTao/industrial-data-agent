from __future__ import annotations

import argparse
import json
from uuid import uuid4

import httpx


def create_fixture(base_url: str) -> dict[str, object]:
    suffix = uuid4().hex[:8]
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        agent = client.post(
            "/api/agent",
            json={
                "name": f"Redis Failover Agent {suffix}",
                "description": "Disposable Redis failover check",
                "status": "draft",
            },
        )
        agent.raise_for_status()
        agent_id = agent.json()["id"]
        chat = client.post(f"/api/agent/{agent_id}/sessions", json={"title": "Failover"})
        chat.raise_for_status()
        session_id = chat.json()["id"]
        message = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"role": "user", "content": "durable in MySQL", "messageType": "text"},
        )
        message.raise_for_status()
        # Prime Redis so the probe proves that a stale hot value is not required.
        client.get(f"/api/sessions/{session_id}/messages").raise_for_status()
        return {"agentId": agent_id, "sessionId": session_id}


def probe_fallback(base_url: str, session_id: str) -> dict[str, object]:
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        messages = client.get(f"/api/sessions/{session_id}/messages")
        messages.raise_for_status()
        assert [item["content"] for item in messages.json()] == ["durable in MySQL"]
        health = client.get("/health")
        health.raise_for_status()
        payload = health.json()
        assert payload["management"]["ready"] is True
        assert payload["redis"]["ready"] is False
        assert payload["status"] == "degraded"
        return {
            "mysqlFallback": True,
            "messageCount": len(messages.json()),
            "redisReady": payload["redis"]["ready"],
            "status": payload["status"],
        }


def cleanup(base_url: str, agent_id: int, session_id: str) -> None:
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        client.delete(f"/api/sessions/{session_id}")
        client.delete(f"/api/agent/{agent_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Support a controlled Redis outage smoke test")
    parser.add_argument("mode", choices=("create", "probe", "cleanup"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8065")
    parser.add_argument("--agent-id", type=int)
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.mode == "create":
        print(json.dumps(create_fixture(args.base_url)))
        return
    if not args.session_id:
        parser.error("--session-id is required")
    if args.mode == "probe":
        print(json.dumps(probe_fallback(args.base_url, args.session_id)))
        return
    if args.agent_id is None:
        parser.error("--agent-id is required")
    cleanup(args.base_url, args.agent_id, args.session_id)
    print(json.dumps({"cleaned": True}))


if __name__ == "__main__":
    main()
