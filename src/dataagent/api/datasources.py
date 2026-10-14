from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.connectors import SUPPORTED_DATASOURCES, datasource_type, password_safe_view
from dataagent.database import get_session
from dataagent.errors import ConflictError, NotFoundError
from dataagent.models import Datasource, LogicalRelation
from dataagent.responses import fail, ok
from dataagent.schemas import DatasourcePayload, LogicalRelationPayload, LogicalRelationSavePayload
from dataagent.serialization import serialize

router = APIRouter(prefix="/api/datasource", tags=["datasource"])


async def _source(session: AsyncSession, source_id: int) -> Datasource:
    value = await session.get(Datasource, source_id)
    if value is None:
        raise NotFoundError(f"Datasource {source_id} not found")
    return value


@router.get("/types")
async def types():
    # H2 is exposed because a working adapter exists; this closes the upstream
    # handler/enumeration mismatch instead of hiding a real capability.
    return ok(SUPPORTED_DATASOURCES, "获取数据源类型成功")


@router.get("")
async def list_datasources(
    status: str | None = None,
    type: str | None = None,  # noqa: A002 - compatibility query name
    session: AsyncSession = Depends(get_session),
):
    statement = select(Datasource)
    if status:
        statement = statement.where(Datasource.status == status)
    elif type:
        statement = statement.where(Datasource.type == type)
    rows = (await session.execute(statement.order_by(Datasource.id.desc()))).scalars()
    return [password_safe_view(item) for item in rows]


@router.get("/{source_id:int}")
async def get_datasource(source_id: int, session: AsyncSession = Depends(get_session)):
    value = await _source(session, source_id)
    return password_safe_view(value)


@router.get("/{source_id:int}/tables")
async def datasource_tables(source_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    value = await _source(session, source_id)
    return await request.app.state.connectors.for_source(value).tables()


@router.post("")
async def create_datasource(payload: DatasourcePayload, session: AsyncSession = Depends(get_session)):
    data = payload.model_dump()
    data["type"] = datasource_type(payload.type)
    value = Datasource(**data)
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return password_safe_view(value)


@router.put("/{source_id:int}")
async def update_datasource(
    source_id: int, payload: DatasourcePayload, session: AsyncSession = Depends(get_session)
):
    value = await _source(session, source_id)
    data = payload.model_dump()
    data["type"] = datasource_type(payload.type)
    if not data["password"] or data["password"].startswith("****"):
        data.pop("password")
    for key, item in data.items():
        setattr(value, key, item)
    await session.commit()
    await session.refresh(value)
    return password_safe_view(value)


@router.delete("/{source_id:int}")
async def delete_datasource(source_id: int, session: AsyncSession = Depends(get_session)):
    value = await _source(session, source_id)
    await session.delete(value)
    await session.commit()
    return ok(None, "数据源删除成功")


@router.post("/{source_id:int}/test")
async def test_datasource(source_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    value = await _source(session, source_id)
    try:
        success = await request.app.state.connectors.for_source(value).test()
    except Exception as exc:
        value.test_status = "failed"
        await session.commit()
        return fail("连接测试失败", status_code=200, data={"connected": False, "error": str(exc)})
    value.test_status = "success" if success else "failed"
    if success:
        value.status = "active"
    await session.commit()
    if success:
        return ok({"connected": True}, "连接测试成功")
    return fail("连接测试失败", status_code=200, data={"connected": False})


@router.get("/{source_id:int}/tables/{table_name}/columns")
async def table_columns(
    source_id: int,
    table_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    value = await _source(session, source_id)
    values = await request.app.state.connectors.for_source(value).columns(table_name)
    # Java endpoint returns names only, while detailed metadata remains internal.
    return ok([item["name"] for item in values], "获取字段列表成功")


@router.get("/{source_id:int}/logical-relations")
async def logical_relations(source_id: int, session: AsyncSession = Depends(get_session)):
    await _source(session, source_id)
    rows = (
        await session.execute(
            select(LogicalRelation).where(
                LogicalRelation.datasource_id == source_id, LogicalRelation.is_deleted == 0
            )
        )
    ).scalars()
    return ok([serialize(item) for item in rows], "success get logical relations")


@router.post("/{source_id:int}/logical-relations")
async def add_logical_relation(
    source_id: int,
    payload: LogicalRelationPayload,
    session: AsyncSession = Depends(get_session),
):
    await _source(session, source_id)
    duplicate = (
        await session.execute(
            select(LogicalRelation.id).where(
                LogicalRelation.datasource_id == source_id,
                LogicalRelation.source_table_name == payload.source_table_name,
                LogicalRelation.source_column_name == payload.source_column_name,
                LogicalRelation.target_table_name == payload.target_table_name,
                LogicalRelation.target_column_name == payload.target_column_name,
                LogicalRelation.is_deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError("该逻辑外键关系已存在")
    value = LogicalRelation(datasource_id=source_id, **payload.model_dump())
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return ok(serialize(value), "success create logical relation")


async def _relation(session: AsyncSession, source_id: int, relation_id: int) -> LogicalRelation:
    value = (
        await session.execute(
            select(LogicalRelation).where(
                LogicalRelation.id == relation_id,
                LogicalRelation.datasource_id == source_id,
                LogicalRelation.is_deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if value is None:
        raise NotFoundError("Logical relation not found")
    return value


@router.put("/{source_id:int}/logical-relations/{relation_id:int}")
async def update_logical_relation(
    source_id: int,
    relation_id: int,
    payload: LogicalRelationPayload,
    session: AsyncSession = Depends(get_session),
):
    value = await _relation(session, source_id, relation_id)
    for key, item in payload.model_dump().items():
        setattr(value, key, item)
    await session.commit()
    await session.refresh(value)
    return ok(serialize(value), "success update logical relation")


@router.delete("/{source_id:int}/logical-relations/{relation_id:int}")
async def delete_logical_relation(
    source_id: int, relation_id: int, session: AsyncSession = Depends(get_session)
):
    value = await _relation(session, source_id, relation_id)
    value.is_deleted = 1
    await session.commit()
    return ok(None, "success delete logical relation")


@router.put("/{source_id:int}/logical-relations")
async def replace_logical_relations(
    source_id: int,
    payload: list[LogicalRelationSavePayload],
    session: AsyncSession = Depends(get_session),
):
    await _source(session, source_id)
    existing = (
        (
            await session.execute(
                select(LogicalRelation).where(
                    LogicalRelation.datasource_id == source_id,
                    LogicalRelation.is_deleted == 0,
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_id = {item.id: item for item in existing}
    incoming_ids = {item.id for item in payload if item.id in existing_by_id}
    for item in existing:
        if item.id not in incoming_ids:
            item.is_deleted = 1

    values: list[LogicalRelation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in payload:
        key = (
            item.source_table_name,
            item.source_column_name,
            item.target_table_name,
            item.target_column_name,
        )
        if key in seen:
            continue
        seen.add(key)
        data = item.model_dump(exclude={"id"})
        value = existing_by_id.get(item.id) if item.id is not None else None
        if value is None:
            value = LogicalRelation(datasource_id=source_id, **data)
            session.add(value)
        else:
            for field, field_value in data.items():
                setattr(value, field, field_value)
            value.is_deleted = 0
        values.append(value)
    await session.commit()
    for value in values:
        await session.refresh(value)
    return ok([serialize(item) for item in values], "success save logical relations")
