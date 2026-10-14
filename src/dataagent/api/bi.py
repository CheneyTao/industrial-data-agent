from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.api.graph import _authorize_agent
from dataagent.bi.contracts import BIRequest, Contract, DashboardRequest
from dataagent.bi.tasks import TERMINAL
from dataagent.database import get_session
from dataagent.errors import DataAgentError
from dataagent.schemas import GraphRequest

router = APIRouter(prefix="/api/bi", tags=["bi"])


async def authorize(
    request: Request,
    agent_id: int = Query(..., ge=1),
    x_api_key: str | None = Header(None),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> int:
    await _authorize_agent(agent_id, session, x_api_key, authorization)
    return agent_id


@router.get("/datasets")
async def datasets(request: Request) -> Any:
    return request.app.state.graph.catalog.listing()


@router.get("/datasets/{dataset_id}")
async def dataset(dataset_id: str, request: Request) -> Any:
    return request.app.state.graph.catalog.get(dataset_id).definition().model_dump()


@router.get("/datasets/{dataset_id}/metrics")
async def metrics(dataset_id: str, request: Request) -> Any:
    return [m.model_dump() for m in request.app.state.graph.catalog.get(dataset_id).metrics()]


@router.post("/datasets/{dataset_id}/dashboard")
async def dashboard(dataset_id: str, payload: DashboardRequest, request: Request) -> Any:
    try:
        return await request.app.state.graph.catalog.get(dataset_id).dashboard(
            payload.filters, payload.feature, payload.dimension
        )
    except ValidationError as exc:
        raise DataAgentError(str(exc)) from exc


@router.post("/datasets/{dataset_id}/entities/{entity_id}")
async def entity(dataset_id: str, entity_id: int, payload: DashboardRequest, request: Request) -> Any:
    adapter = request.app.state.graph.catalog.get(dataset_id)
    method = getattr(adapter, "entity", None)
    if not method:
        raise DataAgentError("该数据集使用原有设备详情接口")
    try:
        return await method(entity_id, payload.filters, payload.feature or adapter.features()[0])
    except ValidationError as exc:
        raise DataAgentError(str(exc)) from exc


@router.post("/plan")
async def plan(
    payload: BIRequest,
    request: Request,
    x_api_key: str | None = Header(None),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Any:
    await _authorize_agent(payload.agent_id, session, x_api_key, authorization)
    try:
        return await request.app.state.graph.bi.plan(payload, session, request.app.state.llm)
    except ValidationError as exc:
        raise DataAgentError(str(exc)) from exc


@router.post("/analyze", status_code=202)
async def analyze(
    payload: BIRequest,
    request: Request,
    x_api_key: str | None = Header(None),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Any:
    await _authorize_agent(payload.agent_id, session, x_api_key, authorization)
    if payload.dataset_id:
        adapter = request.app.state.graph.catalog.get(payload.dataset_id)
        try:
            if payload.filters is not None:
                adapter.filter_model.model_validate(payload.filters)
            if payload.plan:
                adapter.validate(payload.plan)
        except ValidationError as exc:
            raise DataAgentError(str(exc)) from exc
    return await request.app.state.graph.start_bi(
        GraphRequest(
            agent_id=str(payload.agent_id),
            query=payload.question,
            conversation_id=payload.conversation_id,
            bi_request=payload.model_dump(mode="json"),
        )
    )


@router.get("/tasks/{task_id}")
async def task(task_id: str, request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.graph.tasks.get(task_id, agent_id)


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, request: Request, agent_id: int = Depends(authorize)) -> Any:
    graph = request.app.state.graph
    await graph.tasks.cancel(task_id, agent_id)
    await graph.stop(thread_id=task_id, conversation_id=None)
    return await graph.tasks.get(task_id, agent_id)


class ReviewRequest(Contract):
    approved: bool = True
    feedback: str = ""


@router.post("/tasks/{task_id}/resume", status_code=202)
async def resume(
    task_id: str, payload: ReviewRequest, request: Request, agent_id: int = Depends(authorize)
) -> Any:
    graph = request.app.state.graph
    task_state = await graph.tasks.get(task_id, agent_id)
    if task_state["status"] != "WAITING":
        raise DataAgentError("Only a waiting task can be resumed", status_code=409)
    return await graph.start_bi(
        GraphRequest(
            agent_id=str(agent_id),
            query="审核 BI 分析计划",
            thread_id=task_id,
            conversation_id=task_state["conversation_id"],
            human_feedback_content=payload.feedback.strip()
            or ("批准执行" if payload.approved else "请修订计划"),
            rejected_plan=not payload.approved,
        )
    )


@router.get("/tasks/{task_id}/events")
async def events(task_id: str, request: Request, agent_id: int = Depends(authorize)) -> Any:
    await request.app.state.graph.tasks.get(task_id, agent_id)

    async def stream():
        last = ""
        while not await request.is_disconnected():
            value = await request.app.state.graph.tasks.get(task_id, agent_id)
            content = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if content != last:
                event = value["status"].lower() if value["status"] in TERMINAL else "progress"
                yield f"event: {event}\ndata: {content}\n\n"
                last = content
            else:
                yield ": keep-alive\n\n"
            if value["status"] in TERMINAL | {"WAITING"}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
