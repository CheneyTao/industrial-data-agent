from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any


class SessionEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish_title(self, agent_id: int, session_id: str, title: str) -> None:
        event = {"sessionId": session_id, "title": title}
        async with self._lock:
            subscribers = list(self._subscribers.get(agent_id, set()))
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop stale UI notifications rather than blocking message persistence.
                pass

    async def subscribe(self, agent_id: int) -> AsyncIterator[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers[agent_id].add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    yield None
        finally:
            async with self._lock:
                self._subscribers[agent_id].discard(queue)
                if not self._subscribers[agent_id]:
                    self._subscribers.pop(agent_id, None)
