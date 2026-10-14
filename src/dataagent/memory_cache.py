from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from dataagent.config import Settings
from dataagent.errors import ConfigurationError

logger = logging.getLogger(__name__)

CONVERSATION_CONTEXT = "conversation-context-v1"
GRAPH_CHECKPOINT = "graph-checkpoint-v1"
CHAT_MESSAGES = "chat-messages-v1"
AGENT_SESSIONS = "agent-sessions-v1"


class RedisMemoryCache:
    """Redis-backed read-through cache with MySQL-safe invalidation semantics.

    MySQL remains the source of truth. Reads use a per-key Redis lock to avoid
    cache stampedes; mutations commit MySQL first and then take the same lock
    before deleting or replacing the cached value. If Redis is unavailable,
    callers transparently execute their database loader instead.
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self._started = False
        self._closed = False
        self._retry_after = 0.0
        self._pending_invalidations: set[str] = set()
        self._pending_lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._errors = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None or bool(self.settings.redis_url.strip())

    @property
    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "errors": self._errors,
            "pendingInvalidations": len(self._pending_invalidations),
        }

    def cache_identity(self, bucket: str, identity: str) -> str:
        digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()
        prefix = self.settings.redis_key_prefix.strip(": ") or "dataagent"
        return f"{prefix}:{bucket}:{digest}"

    async def start(self) -> None:
        if self._started or not self.enabled:
            self._started = True
            return
        try:
            client = self._ensure_client()
            await client.ping()
            self._started = True
            self._retry_after = 0.0
            self._last_error = None
            await self._flush_pending(client)
        except Exception as exc:
            self._mark_error(exc)
            if self.settings.redis_required:
                raise ConfigurationError(f"Required Redis is unavailable: {exc}") from exc

    async def close(self) -> None:
        self._closed = True
        if self._client is None or not self._owns_client:
            return
        close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    async def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "required": self.settings.redis_required,
                "ready": True,
                **self.stats,
            }
        try:
            client = self._ensure_client()
            await client.ping()
            self._retry_after = 0.0
            self._last_error = None
            await self._flush_pending(client)
            ready = True
        except Exception as exc:
            self._mark_error(exc)
            ready = False
        result: dict[str, Any] = {
            "enabled": True,
            "required": self.settings.redis_required,
            "ready": ready,
            **self.stats,
        }
        if self._last_error:
            result["error"] = self._last_error
        return result

    async def get_or_load(
        self,
        bucket: str,
        identity: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Return a cached JSON value, or load it without making Redis mandatory."""

        key = self.cache_identity(bucket, identity)
        client = await self._ready_client()
        if client is None or key in self._pending_invalidations:
            self._misses += 1
            return await loader()

        try:
            cached = await self._get(client, key)
        except Exception as exc:
            self._mark_error(exc)
            self._misses += 1
            return await loader()
        if cached is not None:
            self._hits += 1
            return cached

        lock = client.lock(
            f"{key}:lock",
            timeout=self.settings.redis_lock_timeout_seconds,
            blocking_timeout=self.settings.redis_lock_wait_seconds,
        )
        acquired = False
        try:
            acquired = bool(await lock.acquire())
        except Exception as exc:
            self._mark_error(exc)
            self._misses += 1
            return await loader()

        if not acquired:
            self._misses += 1
            return await loader()

        try:
            try:
                cached = await self._get(client, key)
            except Exception as exc:
                self._mark_error(exc)
                self._misses += 1
                return await loader()
            if cached is not None:
                self._hits += 1
                return cached
            self._misses += 1
            value = await loader()
            if value is not None and key not in self._pending_invalidations:
                try:
                    await client.set(
                        key,
                        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str),
                        ex=ttl_seconds,
                    )
                except Exception as exc:
                    self._mark_error(exc)
            return value
        finally:
            await self._release_lock(lock)

    async def put(self, bucket: str, identity: str, value: Any, ttl_seconds: int) -> None:
        if not self.enabled:
            return
        key = self.cache_identity(bucket, identity)
        async with self._pending_lock:
            self._pending_invalidations.add(key)
        client = await self._ready_client(flush_pending=False)
        if client is None:
            return
        lock = client.lock(
            f"{key}:lock",
            timeout=self.settings.redis_lock_timeout_seconds,
            blocking_timeout=self.settings.redis_lock_wait_seconds,
        )
        acquired = False
        try:
            acquired = bool(await lock.acquire())
            if not acquired:
                return
            await client.set(
                key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str),
                ex=ttl_seconds,
            )
            async with self._pending_lock:
                self._pending_invalidations.discard(key)
        except Exception as exc:
            self._mark_error(exc)
        finally:
            if acquired:
                await self._release_lock(lock)

    async def invalidate(self, bucket: str, identity: str) -> None:
        if not self.enabled:
            return
        key = self.cache_identity(bucket, identity)
        async with self._pending_lock:
            self._pending_invalidations.add(key)
        client = await self._ready_client(flush_pending=False)
        if client is not None:
            await self._invalidate_key(client, key)

    async def invalidate_many(self, bucket: str, identities: Iterable[str]) -> None:
        for identity in dict.fromkeys(identities):
            await self.invalidate(bucket, identity)

    async def _get(self, client: Any, key: str) -> Any | None:
        try:
            raw = await client.get(key)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("Discarding invalid Redis cache payload for %s: %s", key, exc)
            await client.delete(key)
            return None

    async def _ready_client(self, *, flush_pending: bool = True) -> Any | None:
        if not self.enabled or self._closed or time.monotonic() < self._retry_after:
            return None
        try:
            client = self._ensure_client()
            if not self._started:
                await client.ping()
                self._started = True
            if flush_pending:
                await self._flush_pending(client)
            self._retry_after = 0.0
            self._last_error = None
            return client
        except Exception as exc:
            self._mark_error(exc)
            return None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError("Redis support requires the 'redis' package") from exc
        self._client = Redis.from_url(
            self.settings.redis_url,
            password=self.settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=self.settings.redis_connect_timeout_seconds,
            socket_timeout=self.settings.redis_socket_timeout_seconds,
            health_check_interval=30,
        )
        return self._client

    async def _flush_pending(self, client: Any) -> None:
        async with self._pending_lock:
            keys = list(self._pending_invalidations)
        for key in keys:
            if not await self._invalidate_key(client, key):
                break

    async def _invalidate_key(self, client: Any, key: str) -> bool:
        lock = client.lock(
            f"{key}:lock",
            timeout=self.settings.redis_lock_timeout_seconds,
            blocking_timeout=self.settings.redis_lock_wait_seconds,
        )
        acquired = False
        try:
            acquired = bool(await lock.acquire())
            if not acquired:
                return False
            await client.delete(key)
            async with self._pending_lock:
                self._pending_invalidations.discard(key)
            return True
        except Exception as exc:
            self._mark_error(exc)
            return False
        finally:
            if acquired:
                await self._release_lock(lock)

    async def _release_lock(self, lock: Any) -> None:
        try:
            await lock.release()
        except Exception as exc:
            # The lock has a bounded TTL; a lost Redis connection cannot leave
            # it permanently held. Keep the cache in fallback mode briefly.
            self._mark_error(exc)

    def _mark_error(self, exc: Exception) -> None:
        self._errors += 1
        self._last_error = str(exc)
        self._retry_after = time.monotonic() + self.settings.redis_retry_interval_seconds
        logger.warning("Redis memory cache unavailable; falling back to MySQL: %s", exc)
