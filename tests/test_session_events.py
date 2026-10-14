from __future__ import annotations

import asyncio

from dataagent.session_events import SessionEventBroker


async def test_session_title_events_are_isolated_by_agent_and_unregister_cleanly() -> None:
    broker = SessionEventBroker()
    subscription = broker.subscribe(4)
    pending = asyncio.create_task(anext(subscription))
    await asyncio.sleep(0)

    await broker.publish_title(5, "wrong-session", "must not leak")
    await broker.publish_title(4, "session-1", "季度收入")

    assert await asyncio.wait_for(pending, timeout=1) == {
        "sessionId": "session-1",
        "title": "季度收入",
    }
    await subscription.aclose()
    assert 4 not in broker._subscribers
