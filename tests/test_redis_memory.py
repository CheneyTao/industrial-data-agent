from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import httpx
from fastapi import FastAPI

from dataagent.config import Settings
from dataagent.memory_cache import CHAT_MESSAGES, RedisMemoryCache


class _FakeLock:
    def __init__(self, lock: asyncio.Lock, blocking_timeout: float) -> None:
        self._lock = lock
        self._blocking_timeout = blocking_timeout

    async def acquire(self) -> bool:
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self._blocking_timeout or 0.001)
        except TimeoutError:
            return False
        return True

    async def release(self) -> None:
        self._lock.release()


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.fail = False
        self.closed = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis unavailable")

    async def ping(self) -> bool:
        self._check()
        return True

    async def get(self, key: str) -> str | None:
        self._check()
        return self.data.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self._check()
        assert ex > 0
        self.data[key] = value
        return True

    async def delete(self, key: str) -> int:
        self._check()
        return int(self.data.pop(key, None) is not None)

    def lock(self, key: str, *, timeout: float, blocking_timeout: float) -> _FakeLock:
        assert timeout > 0
        return _FakeLock(self.locks[key], blocking_timeout)

    async def aclose(self) -> None:
        self.closed = True


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        redis_url="redis://fake:6379/0",
        redis_retry_interval_seconds=0,
        redis_lock_wait_seconds=0.1,
    )


async def test_disabled_redis_is_a_true_noop() -> None:
    cache = RedisMemoryCache(Settings(_env_file=None, redis_url=""))
    await cache.invalidate("test", "one")
    assert cache.stats["pendingInvalidations"] == 0

    async def loader() -> str:
        return "database"

    assert await cache.get_or_load("test", "one", 60, loader) == "database"


async def test_redis_cache_reads_through_and_invalidates_after_mutation() -> None:
    client = _FakeRedis()
    cache = RedisMemoryCache(_settings(), client)
    loads = 0

    async def loader() -> dict[str, Any]:
        nonlocal loads
        loads += 1
        return {"revision": loads}

    first = await cache.get_or_load("test", "one", 60, loader)
    second = await cache.get_or_load("test", "one", 60, loader)
    assert first == second == {"revision": 1}
    assert loads == 1
    assert cache.stats["hits"] == 1

    await cache.invalidate("test", "one")
    third = await cache.get_or_load("test", "one", 60, loader)
    assert third == {"revision": 2}
    assert loads == 2


async def test_redis_failure_falls_back_once_and_replays_pending_invalidation() -> None:
    client = _FakeRedis()
    cache = RedisMemoryCache(_settings(), client)
    key = cache.cache_identity("test", "failure")
    client.data[key] = '{"stale":true}'
    client.fail = True
    loads = 0

    async def loader() -> dict[str, Any]:
        nonlocal loads
        loads += 1
        return {"database": True}

    assert await cache.get_or_load("test", "failure", 60, loader) == {"database": True}
    assert loads == 1
    await cache.invalidate("test", "failure")
    assert cache.stats["pendingInvalidations"] == 1

    client.fail = False
    assert await cache.get_or_load("test", "failure", 60, loader) == {"database": True}
    assert key in client.data
    assert cache.stats["pendingInvalidations"] == 0


async def test_chat_message_cache_is_invalidated_by_fastapi_writes(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    fake_redis = _FakeRedis()
    cache = RedisMemoryCache(_settings(), fake_redis)
    app.state.memory_cache = cache
    app.state.graph.memory_cache = cache

    agent = (
        await client.post(
            "/api/agent",
            json={"name": "Redis Agent", "description": "cache test", "status": "draft"},
        )
    ).json()
    chat = (await client.post(f"/api/agent/{agent['id']}/sessions")).json()
    session_id = chat["id"]

    await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"role": "user", "content": "first", "messageType": "text"},
    )
    assert len((await client.get(f"/api/sessions/{session_id}/messages")).json()) == 1
    hits_before = cache.stats["hits"]
    assert len((await client.get(f"/api/sessions/{session_id}/messages")).json()) == 1
    assert cache.stats["hits"] == hits_before + 1

    await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"role": "assistant", "content": "second", "messageType": "text"},
    )
    messages = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    assert [item["content"] for item in messages] == ["first", "second"]

    key = cache.cache_identity(CHAT_MESSAGES, session_id)
    assert key in fake_redis.data
    assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 200
    assert key not in fake_redis.data
    assert (await client.get(f"/api/sessions/{session_id}/messages")).status_code == 404
