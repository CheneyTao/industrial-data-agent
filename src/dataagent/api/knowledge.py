from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.database import get_session
from dataagent.documents import read_upload_limited
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.models import Agent, AgentKnowledge, BusinessKnowledge
from dataagent.responses import ok, page
from dataagent.schemas import (
    AgentKnowledgeQuery,
    AgentKnowledgeUpdate,
    BusinessKnowledgeCreate,
    BusinessKnowledgeUpdate,
)
from dataagent.serialization import serialize

agent_router = APIRouter(prefix="/api/agent-knowledge", tags=["agent-knowledge"])
business_router = APIRouter(prefix="/api/business-knowledge", tags=["business-knowledge"])

_KNOWLEDGE_TYPES = {"DOCUMENT", "QA", "FAQ"}


def _knowledge_view(item: AgentKnowledge) -> dict[str, Any]:
    value = serialize(item)
    value["isRecall"] = bool(item.is_recall)
    return value


def _business_view(item: BusinessKnowledge) -> dict[str, Any]:
    value = serialize(item)
    value["isRecall"] = bool(item.is_recall)
    return value


async def _agent_item(session: AsyncSession, item_id: int) -> AgentKnowledge:
    value = await session.get(AgentKnowledge, item_id)
    if value is None or value.is_deleted:
        raise NotFoundError("Knowledge not found")
    return value


async def _business_item(session: AsyncSession, item_id: int) -> BusinessKnowledge:
    value = await session.get(BusinessKnowledge, item_id)
    if value is None or value.is_deleted:
        raise NotFoundError("Business knowledge not found")
    return value


async def _index_agent(app: Any, item_id: int) -> None:
    async with app.state.database.session_factory() as session:
        await app.state.knowledge_indexer.index_agent_knowledge(session, item_id)


async def _index_business(app: Any, item_id: int) -> None:
    async with app.state.database.session_factory() as session:
        await app.state.knowledge_indexer.index_business_knowledge(session, item_id)


async def _cleanup_agent(app: Any, item_id: int) -> None:
    async with app.state.database.session_factory() as session:
        item = await session.get(AgentKnowledge, item_id)
        if item is None:
            return
        await app.state.knowledge_indexer.remove_agent_knowledge(item)
        if item.file_path:
            await app.state.file_storage.delete(item.file_path)
        item.is_resource_cleaned = 1
        await session.commit()


@agent_router.get("/agent/{agent_id:int}")
async def list_agent_knowledge_compat(
    agent_id: int,
    type: str | None = None,  # noqa: A002
    status: str | None = None,
    keyword: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    statement = select(AgentKnowledge).where(
        AgentKnowledge.agent_id == agent_id, AgentKnowledge.is_deleted == 0
    )
    if type:
        statement = statement.where(AgentKnowledge.type == type)
    if status:
        statement = statement.where(AgentKnowledge.embedding_status == status)
    if keyword:
        like = f"%{keyword}%"
        statement = statement.where(or_(AgentKnowledge.title.ilike(like), AgentKnowledge.content.ilike(like)))
    rows = (await session.execute(statement.order_by(AgentKnowledge.id.desc()))).scalars()
    return ok([_knowledge_view(item) for item in rows], "查询成功")


@agent_router.get("/statistics/{agent_id:int}")
async def agent_knowledge_statistics(agent_id: int, session: AsyncSession = Depends(get_session)):
    rows = (
        (
            await session.execute(
                select(AgentKnowledge).where(
                    AgentKnowledge.agent_id == agent_id, AgentKnowledge.is_deleted == 0
                )
            )
        )
        .scalars()
        .all()
    )
    status: dict[str, int] = {}
    types: dict[str, int] = {}
    for item in rows:
        status[item.embedding_status or "UNKNOWN"] = status.get(item.embedding_status or "UNKNOWN", 0) + 1
        types[item.type] = types.get(item.type, 0) + 1
    return ok(
        {
            "total": len(rows),
            "recalled": sum(1 for item in rows if item.is_recall),
            "byStatus": status,
            "byType": types,
        },
        "查询成功",
    )


@agent_router.get("/{item_id:int}")
async def get_agent_knowledge(item_id: int, session: AsyncSession = Depends(get_session)):
    return ok(_knowledge_view(await _agent_item(session, item_id)), "查询成功")


@agent_router.post("/create")
async def create_agent_knowledge(
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    content_type = request.headers.get("content-type", "")
    upload: UploadFile | None = None
    if "multipart/form-data" in content_type:
        form = await request.form()
        data = dict(form)
        candidate = data.pop("file", None)
        if candidate is not None and hasattr(candidate, "read"):
            upload = candidate
    else:
        data = await request.json()
    try:
        agent_id = int(data.get("agentId"))
    except (TypeError, ValueError) as exc:
        raise DataAgentError("agentId is required") from exc
    title = str(data.get("title") or "").strip()
    knowledge_type = str(data.get("type") or "").strip().upper()
    if not title or not knowledge_type:
        raise DataAgentError("title and type are required")
    if knowledge_type not in _KNOWLEDGE_TYPES:
        raise DataAgentError("type must be one of DOCUMENT, QA, or FAQ")
    if await session.get(Agent, agent_id) is None:
        raise NotFoundError("Agent not found")

    text_content = str(data.get("content") or "")
    question = str(data.get("question") or "").strip() or None
    if knowledge_type == "DOCUMENT" and upload is None:
        raise DataAgentError("File is required for document type")
    if knowledge_type in {"QA", "FAQ"}:
        if not question:
            raise DataAgentError("Question is required for QA or FAQ type")
        if not text_content.strip():
            raise DataAgentError("Content is required for QA or FAQ type")

    file_path = source_filename = file_type = None
    file_size = None
    if knowledge_type == "DOCUMENT" and upload is not None:
        raw = await read_upload_limited(upload, request.app.state.settings.file_max_size)
        file_size = len(raw)
        source_filename = upload.filename or "upload.bin"
        file_type = upload.content_type
        text_content = await request.app.state.document_extractor.extract(
            source_filename, raw, upload.content_type
        )
        try:
            file_path, _ = await request.app.state.file_storage.save(source_filename, raw)
        except Exception:
            raise
    item = AgentKnowledge(
        agent_id=agent_id,
        title=title,
        type=knowledge_type,
        question=question,
        content=text_content,
        is_recall=int(str(data.get("isRecall", "1")).lower() not in {"0", "false", "no"}),
        embedding_status="PENDING",
        source_filename=source_filename,
        file_path=file_path,
        file_size=file_size,
        file_type=file_type,
        splitter_type=str(data.get("splitterType") or "token"),
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    # Upstream indexes every new item and relies on the dynamic DB filter to hide
    # non-recalled knowledge. This makes later recall toggles immediate and lossless.
    background.add_task(_index_agent, request.app, item.id)
    return ok(_knowledge_view(item), "创建知识成功，后台向量存储开始更新，请耐心等待...")


@agent_router.put("/{item_id:int}")
async def update_agent_knowledge(
    item_id: int,
    payload: AgentKnowledgeUpdate,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    item = await _agent_item(session, item_id)
    # Match StringUtils.hasText in the Java service. QA/FAQ answers are read from
    # the database at recall time, so editing title/content does not re-embed the
    # question vector.
    for key in payload.model_fields_set:
        value = getattr(payload, key)
        if isinstance(value, str) and value.strip():
            setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return ok(_knowledge_view(item), "更新成功")


@agent_router.put("/recall/{item_id:int}")
async def update_agent_recall(
    item_id: int,
    request: Request,
    background: BackgroundTasks,
    is_recall: bool = Query(..., alias="isRecall"),
    session: AsyncSession = Depends(get_session),
):
    item = await _agent_item(session, item_id)
    item.is_recall = int(is_recall)
    await session.commit()
    await session.refresh(item)
    # Keep vectors in place exactly like DynamicFilterService upstream. Search
    # consults the current database recall flag before querying the vector store.
    if is_recall and item.embedding_status != "COMPLETED":
        background.add_task(_index_agent, request.app, item.id)
    return ok(_knowledge_view(item), "更新成功")


@agent_router.delete("/{item_id:int}")
async def delete_agent_knowledge(
    item_id: int,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    item = await _agent_item(session, item_id)
    item.is_deleted = 1
    await session.commit()
    background.add_task(_cleanup_agent, request.app, item.id)
    return ok(True, "删除操作已接收，等待后台删除相关资源...")


@agent_router.post("/query/page")
async def query_agent_knowledge(payload: AgentKnowledgeQuery, session: AsyncSession = Depends(get_session)):
    conditions = [AgentKnowledge.agent_id == payload.agent_id, AgentKnowledge.is_deleted == 0]
    if payload.title:
        conditions.append(AgentKnowledge.title.ilike(f"%{payload.title}%"))
    if payload.type:
        conditions.append(AgentKnowledge.type == payload.type)
    if payload.is_recall is not None:
        conditions.append(AgentKnowledge.is_recall == payload.is_recall)
    if payload.embedding_status:
        conditions.append(AgentKnowledge.embedding_status == payload.embedding_status)
    total = int(
        (
            await session.execute(select(func.count()).select_from(AgentKnowledge).where(*conditions))
        ).scalar_one()
    )
    rows = (
        await session.execute(
            select(AgentKnowledge)
            .where(*conditions)
            .order_by(AgentKnowledge.id.desc())
            .offset((payload.page_num - 1) * payload.page_size)
            .limit(payload.page_size)
        )
    ).scalars()
    return page(
        [_knowledge_view(item) for item in rows],
        total=total,
        page_num=payload.page_num,
        page_size=payload.page_size,
    )


@agent_router.post("/retry-embedding/{item_id:int}")
async def retry_agent_embedding(
    item_id: int,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    item = await _agent_item(session, item_id)
    if item.embedding_status == "PROCESSING":
        raise DataAgentError("Knowledge is processing, please wait")
    if not item.is_recall:
        raise DataAgentError("Knowledge is not recalled, please recall it first")
    item.embedding_status = "PENDING"
    item.error_msg = None
    await session.commit()
    await session.refresh(item)
    background.add_task(_index_agent, request.app, item.id)
    return ok(_knowledge_view(item), "重试向量化操作成功，如果是文件解析需要花费点时间，请耐心等待...")


@business_router.get("")
async def list_business_knowledge(
    agent_id: int = Query(..., alias="agentId"),
    keyword: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    statement = select(BusinessKnowledge).where(
        BusinessKnowledge.agent_id == agent_id, BusinessKnowledge.is_deleted == 0
    )
    if keyword:
        like = f"%{keyword}%"
        statement = statement.where(
            or_(BusinessKnowledge.business_term.ilike(like), BusinessKnowledge.description.ilike(like))
        )
    rows = (await session.execute(statement.order_by(BusinessKnowledge.id.desc()))).scalars()
    return ok([_business_view(item) for item in rows], "success list businessKnowledge")


@business_router.get("/{item_id:int}")
async def get_business_knowledge(item_id: int, session: AsyncSession = Depends(get_session)):
    return ok(_business_view(await _business_item(session, item_id)), "success get businessKnowledge")


@business_router.post("")
async def create_business_knowledge(
    payload: BusinessKnowledgeCreate,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    item = BusinessKnowledge(**payload.model_dump(), embedding_status="PENDING")
    session.add(item)
    await session.commit()
    await session.refresh(item)
    background.add_task(_index_business, request.app, item.id)
    return ok(_business_view(item), "success create businessKnowledge")


@business_router.put("/{item_id:int}")
async def update_business_knowledge(
    item_id: int,
    payload: BusinessKnowledgeUpdate,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    item = await _business_item(session, item_id)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.embedding_status = "PENDING"
    await session.commit()
    await session.refresh(item)
    background.add_task(_index_business, request.app, item.id)
    return ok(_business_view(item), "success update businessKnowledge")


@business_router.delete("/{item_id:int}")
async def delete_business_knowledge(
    item_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    item = await _business_item(session, item_id)
    await request.app.state.knowledge_indexer.remove_business_knowledge(item)
    item.is_deleted = 1
    await session.commit()
    return ok(True, "success delete businessKnowledge")


@business_router.post("/recall/{item_id:int}")
async def recall_business_knowledge(
    item_id: int,
    request: Request,
    background: BackgroundTasks,
    is_recall: bool = Query(..., alias="isRecall"),
    session: AsyncSession = Depends(get_session),
):
    item = await _business_item(session, item_id)
    item.is_recall = int(is_recall)
    await session.commit()
    if is_recall and item.embedding_status != "COMPLETED":
        background.add_task(_index_business, request.app, item.id)
    return ok(True, "success update recall businessKnowledge")


@business_router.post("/refresh-vector-store")
async def refresh_business_vectors(
    request: Request,
    background: BackgroundTasks,
    agent_id: int = Query(..., alias="agentId"),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(BusinessKnowledge).where(
                    BusinessKnowledge.agent_id == agent_id,
                    BusinessKnowledge.is_deleted == 0,
                    BusinessKnowledge.is_recall == 1,
                )
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        item.embedding_status = "PENDING"
        background.add_task(_index_business, request.app, item.id)
    await session.commit()
    return ok(True, "success refresh vector store")


@business_router.post("/retry-embedding/{item_id:int}")
async def retry_business_embedding(
    item_id: int,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    item = await _business_item(session, item_id)
    if item.embedding_status == "PROCESSING":
        raise DataAgentError("BusinessKnowledge is processing, please wait")
    if not item.is_recall:
        raise DataAgentError("BusinessKnowledge is not recalled, please recall it first")
    item.embedding_status = "PENDING"
    item.error_msg = None
    await session.commit()
    background.add_task(_index_business, request.app, item.id)
    return ok(True, "success retry embedding")
