from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.database import get_session
from dataagent.documents import storage_location_from_url
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.models import Agent, AgentKnowledge
from dataagent.responses import ok
from dataagent.schemas import AgentPayload
from dataagent.security import create_api_key, hash_api_key, masked_api_key
from dataagent.serialization import serialize

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _view(agent: Agent) -> dict[str, object]:
    data = serialize(agent, exclude={"api_key"})
    data["apiKey"] = None
    return data


async def _agent(session: AsyncSession, agent_id: int) -> Agent:
    value = await session.get(Agent, agent_id)
    if value is None:
        raise NotFoundError(f"Agent {agent_id} not found")
    return value


@router.get("/list")
async def list_agents(
    status: str | None = None,
    keyword: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    statement = select(Agent)
    if keyword:
        like = f"%{keyword}%"
        statement = statement.where(
            or_(Agent.name.ilike(like), Agent.description.ilike(like), Agent.tags.ilike(like))
        )
    elif status:
        statement = statement.where(Agent.status == status)
    rows = (await session.execute(statement.order_by(Agent.update_time.desc()))).scalars()
    return [_view(item) for item in rows]


@router.get("/{agent_id}")
async def get_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    return _view(await _agent(session, agent_id))


@router.post("")
async def create_agent(
    payload: AgentPayload, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    value = Agent(**payload.model_dump())
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return _view(value)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: int, payload: AgentPayload, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    value = await _agent(session, agent_id)
    # Java's dynamic UPDATE changes only fields present in the request. Respect
    # Pydantic's explicit field set so omitted UI fields do not reset prompt,
    # avatar, admin ownership, or human-review policy to their schema defaults.
    changes = payload.model_dump(include=payload.model_fields_set)
    for key, item in changes.items():
        setattr(value, key, item)
    await session.commit()
    await session.refresh(value)
    return _view(value)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    value = await _agent(session, agent_id)
    knowledge = (
        (await session.execute(select(AgentKnowledge).where(AgentKnowledge.agent_id == agent_id)))
        .scalars()
        .all()
    )
    # External resources are removed before the database owner row, mirroring the
    # upstream transactional intent and avoiding orphaned vectors/files.
    await request.app.state.retrieval.store.delete_by_metadata({"agentId": agent_id})
    for item in knowledge:
        if item.file_path:
            await request.app.state.file_storage.delete(item.file_path)
    avatar_location = storage_location_from_url(request.app.state.settings, value.avatar)
    if avatar_location:
        await request.app.state.file_storage.delete(avatar_location)
    await session.delete(value)
    await session.commit()
    return Response(status_code=200)


@router.post("/{agent_id}/publish")
async def publish_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    value = await _agent(session, agent_id)
    value.status = "published"
    await session.commit()
    await session.refresh(value)
    return _view(value)


@router.post("/{agent_id}/offline")
async def offline_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    value = await _agent(session, agent_id)
    value.status = "offline"
    await session.commit()
    await session.refresh(value)
    return _view(value)


def _key_response(value: Agent, *, api_key: str | None = None) -> dict[str, object]:
    return {"apiKey": api_key, "apiKeyEnabled": value.api_key_enabled}


@router.get("/{agent_id}/api-key")
async def get_api_key(agent_id: int, session: AsyncSession = Depends(get_session)):
    value = await _agent(session, agent_id)
    return ok(_key_response(value, api_key=masked_api_key(value.api_key)), "获取 API Key 成功")


@router.post("/{agent_id}/api-key/generate")
@router.post("/{agent_id}/api-key/reset")
async def generate_or_reset_api_key(agent_id: int, session: AsyncSession = Depends(get_session)):
    value = await _agent(session, agent_id)
    plain = create_api_key()
    value.api_key = hash_api_key(plain)
    value.api_key_enabled = 1
    await session.commit()
    return ok(_key_response(value, api_key=plain), "生成 API Key 成功")


@router.delete("/{agent_id}/api-key")
async def delete_api_key(agent_id: int, session: AsyncSession = Depends(get_session)):
    value = await _agent(session, agent_id)
    value.api_key = None
    value.api_key_enabled = 0
    await session.commit()
    return ok(_key_response(value), "删除 API Key 成功")


@router.post("/{agent_id}/api-key/enable")
async def enable_api_key(
    agent_id: int,
    enabled: bool = Query(...),
    session: AsyncSession = Depends(get_session),
):
    value = await _agent(session, agent_id)
    if enabled and not value.api_key:
        raise DataAgentError("Generate an API key before enabling API key authentication")
    value.api_key_enabled = int(enabled)
    await session.commit()
    masked = "****" if value.api_key else None
    return ok(_key_response(value, api_key=masked), "更新 API Key 状态成功")
