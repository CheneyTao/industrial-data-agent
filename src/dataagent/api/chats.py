from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.database import get_session
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.memory_cache import AGENT_SESSIONS, CHAT_MESSAGES
from dataagent.models import Agent, AgentPresetQuestion, ChatMessage, ChatSession, ConversationTurn
from dataagent.reports import report_html
from dataagent.responses import ok
from dataagent.schemas import ChatMessagePayload, PresetQuestionPayload
from dataagent.serialization import serialize

router = APIRouter(prefix="/api", tags=["chat"])
preset_router = APIRouter(prefix="/api/agent", tags=["preset-question"])


async def _chat_session(session: AsyncSession, session_id: str) -> ChatSession:
    value = await session.get(ChatSession, session_id)
    if value is None or value.status == "deleted":
        raise NotFoundError(f"Chat session {session_id} not found")
    return value


def _message_view(item: ChatMessage) -> dict[str, Any]:
    value = serialize(item, exclude={"metadata_json"})
    value["metadata"] = item.metadata_json
    return value


@router.get("/agent/{agent_id:int}/sessions")
async def agent_sessions(
    agent_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    async def load() -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(ChatSession)
                .where(ChatSession.agent_id == agent_id, ChatSession.status != "deleted")
                .order_by(ChatSession.is_pinned.desc(), ChatSession.update_time.desc())
            )
        ).scalars()
        return [serialize(item) for item in rows]

    value = await request.app.state.memory_cache.get_or_load(
        AGENT_SESSIONS,
        str(agent_id),
        request.app.state.settings.redis_chat_ttl_seconds,
        load,
    )
    return value if isinstance(value, list) else []


@router.post("/agent/{agent_id:int}/sessions")
async def create_chat_session(
    agent_id: int,
    request: Request,
    payload: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),
):
    if await session.get(Agent, agent_id) is None:
        raise NotFoundError(f"Agent {agent_id} not found")
    payload = payload or {}
    value = ChatSession(
        id=str(uuid4()),
        agent_id=agent_id,
        title=str(payload.get("title") or "新会话"),
        status="active",
        user_id=payload.get("userId") if isinstance(payload.get("userId"), int) else None,
    )
    session.add(value)
    await session.commit()
    await session.refresh(value)
    await request.app.state.memory_cache.invalidate(AGENT_SESSIONS, str(agent_id))
    return serialize(value)


@router.delete("/agent/{agent_id:int}/sessions")
async def clear_chat_sessions(
    agent_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    ids = (
        (
            await session.execute(
                select(ChatSession.id).where(
                    ChatSession.agent_id == agent_id, ChatSession.status != "deleted"
                )
            )
        )
        .scalars()
        .all()
    )
    await session.execute(
        update(ChatSession)
        .where(ChatSession.agent_id == agent_id)
        .values(status="deleted", update_time=datetime.now())
    )
    if ids:
        await session.execute(delete(ConversationTurn).where(ConversationTurn.conversation_id.in_(ids)))
    await session.commit()
    await request.app.state.memory_cache.invalidate(AGENT_SESSIONS, str(agent_id))
    await request.app.state.memory_cache.invalidate_many(CHAT_MESSAGES, ids)
    await request.app.state.graph.invalidate_conversation_memory(ids)
    return ok(None, "会话已清空")


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    async def load() -> list[dict[str, Any]]:
        await _chat_session(session, session_id)
        rows = (
            await session.execute(
                select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
            )
        ).scalars()
        return [_message_view(item) for item in rows]

    value = await request.app.state.memory_cache.get_or_load(
        CHAT_MESSAGES,
        session_id,
        request.app.state.settings.redis_chat_ttl_seconds,
        load,
    )
    return value if isinstance(value, list) else []


async def _generate_title(app: Any, session_id: str, content: str) -> None:
    async with app.state.database.session_factory() as session:
        chat = await session.get(ChatSession, session_id)
        if chat is None or chat.status == "deleted":
            return
        try:
            generated = await app.state.llm.complete(
                session,
                system=(
                    "Generate a concise conversation title of at most 20 Chinese characters. "
                    "Output title only."
                ),
                user=content[:1500],
                purpose="session-title",
                temperature=0.2,
            )
            title = str(generated).strip().strip('"“”')[:50] or content.strip()[:20]
        except Exception:
            title = content.strip().replace("\n", " ")[:20] or "新会话"
        chat.title = title
        await session.commit()
        await app.state.memory_cache.invalidate(AGENT_SESSIONS, str(chat.agent_id))
        await app.state.session_events.publish_title(chat.agent_id, session_id, title)


@router.post("/sessions/{session_id}/messages")
async def save_message(
    session_id: str,
    payload: ChatMessagePayload,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    chat = await _chat_session(session, session_id)
    value = ChatMessage(
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        message_type=payload.message_type,
        metadata_json=payload.metadata,
    )
    session.add(value)
    chat.update_time = datetime.now()
    await session.commit()
    await session.refresh(value)
    await request.app.state.memory_cache.invalidate(CHAT_MESSAGES, session_id)
    await request.app.state.memory_cache.invalidate(AGENT_SESSIONS, str(chat.agent_id))
    if payload.title_needed:
        background.add_task(_generate_title, request.app, session_id, payload.content)
    return _message_view(value)


@router.put("/sessions/{session_id}/pin")
async def pin_session(
    session_id: str,
    request: Request,
    is_pinned: bool = Query(..., alias="isPinned"),
    session: AsyncSession = Depends(get_session),
):
    value = await _chat_session(session, session_id)
    value.is_pinned = is_pinned
    await session.commit()
    await request.app.state.memory_cache.invalidate(AGENT_SESSIONS, str(value.agent_id))
    return ok(None, "会话已置顶" if is_pinned else "会话已取消置顶")


@router.put("/sessions/{session_id}/rename")
async def rename_session(
    session_id: str,
    request: Request,
    title: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    if not title.strip():
        raise DataAgentError("标题不能为空")
    value = await _chat_session(session, session_id)
    value.title = title.strip()
    await session.commit()
    await request.app.state.memory_cache.invalidate(AGENT_SESSIONS, str(value.agent_id))
    return ok(None, "会话已重命名")


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    value = await _chat_session(session, session_id)
    agent_id = value.agent_id
    value.status = "deleted"
    await session.execute(delete(ConversationTurn).where(ConversationTurn.conversation_id == session_id))
    await session.commit()
    await request.app.state.memory_cache.invalidate(CHAT_MESSAGES, session_id)
    await request.app.state.memory_cache.invalidate(AGENT_SESSIONS, str(agent_id))
    await request.app.state.graph.invalidate_conversation_memory([session_id])
    return ok(None, "会话已删除")


@router.post("/sessions/{session_id}/reports/html")
async def download_report(session_id: str, request: Request):
    content = (await request.body()).decode("utf-8", errors="replace")
    if not content.strip():
        raise DataAgentError("Report content must not be empty")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return Response(
        report_html(content, request.app.state.settings),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_{timestamp}.html"'},
    )


@router.get("/agent/{agent_id:int}/sessions/stream")
async def session_event_stream(agent_id: int, request: Request):
    async def events():
        async for event in request.app.state.session_events.subscribe(agent_id):
            if await request.is_disconnected():
                break
            if event is None:
                yield ": keep-alive\n\n"
            else:
                yield "event: title-updated\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@preset_router.get("/{agent_id:int}/preset-questions")
async def preset_questions(agent_id: int, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(AgentPresetQuestion)
            .where(AgentPresetQuestion.agent_id == agent_id)
            .order_by(AgentPresetQuestion.sort_order, AgentPresetQuestion.id)
        )
    ).scalars()
    return [serialize(item) for item in rows]


@preset_router.post("/{agent_id:int}/preset-questions")
async def save_preset_questions(
    agent_id: int,
    payload: list[PresetQuestionPayload],
    session: AsyncSession = Depends(get_session),
):
    await session.execute(delete(AgentPresetQuestion).where(AgentPresetQuestion.agent_id == agent_id))
    for index, item in enumerate(payload):
        session.add(
            AgentPresetQuestion(
                agent_id=agent_id,
                question=item.question,
                sort_order=item.sort_order or index,
                is_active=item.is_active,
            )
        )
    await session.commit()
    return {"message": "预设问题保存成功"}


@preset_router.delete("/{agent_id:int}/preset-questions/{question_id:int}")
async def delete_preset_question(
    agent_id: int, question_id: int, session: AsyncSession = Depends(get_session)
):
    value = (
        await session.execute(
            select(AgentPresetQuestion).where(
                AgentPresetQuestion.id == question_id, AgentPresetQuestion.agent_id == agent_id
            )
        )
    ).scalar_one_or_none()
    if value is None:
        raise NotFoundError("Preset question not found")
    await session.delete(value)
    await session.commit()
    return {"message": "预设问题删除成功"}
