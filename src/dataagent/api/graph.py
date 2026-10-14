from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.artifacts import ArtifactStore
from dataagent.database import get_session
from dataagent.errors import NotFoundError
from dataagent.models import Agent
from dataagent.schemas import GraphRequest
from dataagent.security import verify_api_key

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/analysis/runs/{run_id}")
async def analysis_run(
    run_id: str,
    request: Request,
    agent_id: int = Query(..., alias="agentId"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_agent(agent_id, session, x_api_key, authorization)
    return ArtifactStore(request.app.state.settings).load(run_id, agent_id)


@router.get("/analysis/runs/{run_id}/artifacts/{artifact_id}")
async def analysis_artifact(
    run_id: str,
    artifact_id: str,
    request: Request,
    agent_id: int = Query(..., alias="agentId"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_agent(agent_id, session, x_api_key, authorization)
    return ArtifactStore(request.app.state.settings).get(run_id, agent_id, artifact_id)


async def _authorize_agent(
    agent_id: int,
    session: AsyncSession,
    x_api_key: str | None,
    authorization: str | None,
) -> None:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise NotFoundError(f"Agent {agent_id} not found")
    if not agent.api_key_enabled:
        return
    credential = x_api_key
    if not credential and authorization and authorization.lower().startswith("bearer "):
        credential = authorization[7:].strip()
    if not credential or not verify_api_key(credential, agent.api_key):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Invalid agent API credentials")


@router.get(
    "/stream/search",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_search(
    request: Request,
    agent_id: int = Query(..., alias="agentId"),
    query: str = Query(...),
    conversation_id: str | None = Query(None, alias="conversationId"),
    thread_id: str | None = Query(None, alias="threadId"),
    human_feedback: bool = Query(False, alias="humanFeedback"),
    human_feedback_content: str | None = Query(None, alias="humanFeedbackContent"),
    rejected_plan: bool = Query(False, alias="rejectedPlan"),
    nl2sql_only: bool = Query(False, alias="nl2sqlOnly"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_agent(agent_id, session, x_api_key, authorization)
    graph_request = GraphRequest(
        agent_id=str(agent_id),
        conversation_id=conversation_id,
        thread_id=thread_id,
        query=query,
        human_feedback=human_feedback,
        human_feedback_content=human_feedback_content,
        rejected_plan=rejected_plan,
        nl2sql_only=nl2sql_only,
    )

    async def events():
        async for event_name, event in request.app.state.graph.stream(graph_request):
            if await request.is_disconnected():
                break
            payload = json.dumps(event.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
            prefix = f"event: {event_name}\n" if event_name else ""
            yield prefix + f"data: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stream/stop", status_code=204)
async def stop_stream(
    request: Request,
    conversation_id: str = Query(..., alias="conversationId"),
    thread_id: str | None = Query(None, alias="threadId"),
) -> Response:
    await request.app.state.graph.stop(thread_id=thread_id, conversation_id=conversation_id)
    return Response(status_code=204)


@router.post(
    "/stream/search",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_search_body(
    payload: GraphRequest,
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    from dataagent.errors import DataAgentError

    if not payload.agent_id.isdigit() or not payload.query.strip():
        raise DataAgentError("需要有效的智能体及分析问题")
    await _authorize_agent(int(payload.agent_id), session, x_api_key, authorization)
    if payload.attachment_ids:
        if not payload.conversation_id or payload.bi_request:
            raise DataAgentError("附件分析需要聊天会话，且不能同时选择固定 BI 数据集")
        await request.app.state.graph.attachments.resolve(
            session, int(payload.agent_id), payload.conversation_id, payload.attachment_ids
        )

    async def events():
        async for event_name, event in request.app.state.graph.stream(payload):
            if await request.is_disconnected():
                break
            data = json.dumps(event.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
            yield (f"event: {event_name}\n" if event_name else "") + f"data: {data}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
