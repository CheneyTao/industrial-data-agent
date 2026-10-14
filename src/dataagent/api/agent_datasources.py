from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dataagent.connectors import password_safe_view
from dataagent.database import get_session
from dataagent.errors import ConflictError, NotFoundError
from dataagent.models import Agent, AgentDatasource, AgentDatasourceTable, Datasource
from dataagent.responses import ok
from dataagent.schemas import ToggleDatasourceDTO, UpdateDatasourceTablesDTO
from dataagent.serialization import serialize

router = APIRouter(prefix="/api/agent/{agent_id}/datasources", tags=["agent-datasource"])


async def _association(session: AsyncSession, agent_id: int, datasource_id: int) -> AgentDatasource:
    value = (
        await session.execute(
            select(AgentDatasource)
            .where(
                AgentDatasource.agent_id == agent_id,
                AgentDatasource.datasource_id == datasource_id,
            )
            .options(selectinload(AgentDatasource.datasource), selectinload(AgentDatasource.selected_tables))
        )
    ).scalar_one_or_none()
    if value is None:
        raise NotFoundError("Datasource association not found")
    return value


def _view(value: AgentDatasource) -> dict[str, object]:
    return {
        **serialize(value),
        "selectTables": [item.table_name for item in value.selected_tables],
        "datasource": password_safe_view(value.datasource),
    }


@router.post("/init")
async def initialize_schema(agent_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    result = await request.app.state.schema_indexer.initialize(session, agent_id)
    return ok(result, "Schema initialized successfully")


@router.get("")
async def list_associations(agent_id: int, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(AgentDatasource)
            .where(AgentDatasource.agent_id == agent_id)
            .options(selectinload(AgentDatasource.datasource), selectinload(AgentDatasource.selected_tables))
            .order_by(AgentDatasource.id)
        )
    ).scalars()
    return ok([_view(item) for item in rows], "success")


@router.get("/active")
async def get_active(agent_id: int, session: AsyncSession = Depends(get_session)):
    value = (
        await session.execute(
            select(AgentDatasource)
            .where(AgentDatasource.agent_id == agent_id, AgentDatasource.is_active == 1)
            .options(selectinload(AgentDatasource.datasource), selectinload(AgentDatasource.selected_tables))
        )
    ).scalar_one_or_none()
    if value is None:
        raise NotFoundError("Active datasource association not found")
    return ok(_view(value), "success")


@router.post("/{datasource_id:int}")
async def add_association(agent_id: int, datasource_id: int, session: AsyncSession = Depends(get_session)):
    if await session.get(Agent, agent_id) is None or await session.get(Datasource, datasource_id) is None:
        raise NotFoundError("Agent or datasource not found")
    await session.execute(
        update(AgentDatasource).where(AgentDatasource.agent_id == agent_id).values(is_active=0)
    )
    value = (
        await session.execute(
            select(AgentDatasource).where(
                AgentDatasource.agent_id == agent_id,
                AgentDatasource.datasource_id == datasource_id,
            )
        )
    ).scalar_one_or_none()
    if value is None:
        value = AgentDatasource(agent_id=agent_id, datasource_id=datasource_id, is_active=1)
        session.add(value)
    else:
        # Reactivating an existing binding must preserve its table scope. The
        # table-selection endpoint is the sole owner of that configuration;
        # silently clearing it here makes a datasource switch invalidate the
        # Agent's Schema index and causes every later recall to return no tables.
        value.is_active = 1
    await session.commit()
    value = await _association(session, agent_id, datasource_id)
    return ok(_view(value), "Datasource associated")


@router.post("/tables")
async def update_tables(
    agent_id: int,
    payload: UpdateDatasourceTablesDTO,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    value = await _association(session, agent_id, payload.datasource_id)
    available = set(await request.app.state.connectors.for_source(value.datasource).tables())
    invalid = sorted(set(payload.tables) - available)
    if invalid:
        raise NotFoundError(f"Tables do not exist in datasource: {', '.join(invalid)}")
    for table in list(value.selected_tables):
        await session.delete(table)
    await session.flush()
    for table_name in dict.fromkeys(payload.tables):
        session.add(AgentDatasourceTable(agent_datasource_id=value.id, table_name=table_name))
    await session.commit()
    return ok(None, "Datasource tables updated")


@router.delete("/{datasource_id:int}")
async def remove_association(
    agent_id: int, datasource_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    value = await _association(session, agent_id, datasource_id)
    await request.app.state.retrieval.store.delete_by_metadata(
        {"agentId": agent_id, "datasourceId": datasource_id}
    )
    await session.delete(value)
    await session.commit()
    return ok(None, "Datasource association removed")


@router.put("/toggle")
async def toggle_association(
    agent_id: int,
    payload: ToggleDatasourceDTO,
    session: AsyncSession = Depends(get_session),
):
    value = await _association(session, agent_id, payload.datasource_id)
    if payload.is_active:
        active = (
            await session.execute(
                select(AgentDatasource).where(
                    AgentDatasource.agent_id == agent_id,
                    AgentDatasource.is_active == 1,
                    AgentDatasource.datasource_id != payload.datasource_id,
                )
            )
        ).scalar_one_or_none()
        if active:
            raise ConflictError("同一智能体下只能启用一个数据源，请先禁用其他数据源后再启用此数据源")
    value.is_active = int(payload.is_active)
    await session.commit()
    value = await _association(session, agent_id, payload.datasource_id)
    return ok(_view(value), "Datasource status updated")
