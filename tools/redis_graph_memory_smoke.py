from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import select

from dataagent.config import get_settings
from dataagent.database import Database
from dataagent.memory_cache import RedisMemoryCache
from dataagent.models import ConversationTurn, GraphCheckpoint
from dataagent.workflow.engine import GraphEngine


async def run() -> dict[str, object]:
    settings = get_settings()
    database = Database(settings)
    cache = RedisMemoryCache(settings)
    graph = GraphEngine(settings, database, memory_cache=cache)
    suffix = uuid4().hex
    conversation_id = f"redis-memory-smoke-{suffix}"
    thread_id = f"redis-checkpoint-smoke-{suffix}"
    try:
        await database.create_schema()
        await cache.start()
        async with database.session_factory() as session:
            session.add(
                ConversationTurn(
                    conversation_id=conversation_id,
                    question="验证 Redis 上下文",
                    planner_output="1. 使用 SQL 查询验证数据",
                )
            )
            await session.commit()

            first_context = await graph._conversation_context(session, conversation_id)
            hits_before = cache.stats["hits"]
            second_context = await graph._conversation_context(session, conversation_id)
            assert second_context == first_context
            assert cache.stats["hits"] == hits_before + 1

            await graph._save_checkpoint(
                session,
                thread_id,
                conversation_id,
                "redis-smoke-agent",
                "HUMAN_FEEDBACK_NODE",
                {"INPUT": "验证 checkpoint", "THREAD_ID": thread_id},
            )
            checkpoint_hits_before = cache.stats["hits"]
            checkpoint = await graph._load_checkpoint(session, thread_id)
            assert checkpoint is not None
            assert checkpoint["next_node"] == "HUMAN_FEEDBACK_NODE"
            assert cache.stats["hits"] == checkpoint_hits_before + 1

        await graph._delete_checkpoint(thread_id)
        async with database.session_factory() as session:
            assert await session.get(GraphCheckpoint, thread_id) is None
            assert await graph._load_checkpoint(session, thread_id) is None
            await graph._restart_last_turn(session, conversation_id)
            assert await graph._conversation_context(session, conversation_id) == ""
            residual = (
                await session.execute(
                    select(ConversationTurn.id).where(
                        ConversationTurn.conversation_id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            assert residual is None

        return {
            "redisReady": True,
            "conversationContextHit": True,
            "checkpointHit": True,
            "checkpointInvalidation": True,
            "conversationInvalidation": True,
            **cache.stats,
        }
    finally:
        await cache.close()
        await graph.telemetry.close()
        await database.close()


def main() -> None:
    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
