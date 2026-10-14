from __future__ import annotations

import argparse
from typing import Any
from uuid import uuid4

import httpx


def _redis_stats(client: httpx.Client) -> dict[str, Any]:
    response = client.get("/health")
    response.raise_for_status()
    health = response.json()
    assert health["redis"]["enabled"] is True
    assert health["redis"]["ready"] is True
    return health["redis"]


def run(base_url: str) -> dict[str, Any]:
    suffix = uuid4().hex[:8]
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        before = _redis_stats(client)
        agent = client.post(
            "/api/agent",
            json={
                "name": f"Redis Smoke Agent {suffix}",
                "description": "Disposable Redis integration check",
                "status": "draft",
            },
        )
        agent.raise_for_status()
        agent_id = agent.json()["id"]
        session_id: str | None = None
        try:
            chat = client.post(f"/api/agent/{agent_id}/sessions", json={"title": "Redis smoke"})
            chat.raise_for_status()
            session_id = chat.json()["id"]

            saved = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"role": "user", "content": "cache me", "messageType": "text"},
            )
            saved.raise_for_status()

            first = client.get(f"/api/sessions/{session_id}/messages")
            second = client.get(f"/api/sessions/{session_id}/messages")
            first.raise_for_status()
            second.raise_for_status()
            assert first.json() == second.json()
            assert len(second.json()) == 1

            after_hit = _redis_stats(client)
            assert after_hit["hits"] > before["hits"]
            assert after_hit["pendingInvalidations"] == 0

            deleted = client.delete(f"/api/sessions/{session_id}")
            deleted.raise_for_status()
            missing = client.get(f"/api/sessions/{session_id}/messages")
            assert missing.status_code == 404
            final = _redis_stats(client)
            return {
                "redisReady": final["ready"],
                "hitDelta": after_hit["hits"] - before["hits"],
                "missDelta": after_hit["misses"] - before["misses"],
                "invalidationVerified": True,
                "pendingInvalidations": final["pendingInvalidations"],
            }
        finally:
            if session_id is not None:
                client.delete(f"/api/sessions/{session_id}")
            client.delete(f"/api/agent/{agent_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify live Redis cache hit and invalidation behavior")
    parser.add_argument("--base-url", default="http://127.0.0.1:8065")
    args = parser.parse_args()
    print(run(args.base_url))


if __name__ == "__main__":
    main()
