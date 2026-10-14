from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.database import get_session
from dataagent.errors import ConflictError, DataAgentError, NotFoundError
from dataagent.models import ModelConfig, UserPromptConfig
from dataagent.responses import fail, ok
from dataagent.schemas import ModelConfigDTO, PromptConfigDTO, mask_secret
from dataagent.serialization import serialize

model_router = APIRouter(prefix="/api/model-config", tags=["model-config"])
prompt_router = APIRouter(prefix="/api/prompt-config", tags=["prompt-config"])


def _model_view(value: ModelConfig) -> dict[str, object]:
    result = serialize(value, exclude={"api_key", "proxy_password", "is_deleted"})
    result["apiKey"] = mask_secret(value.api_key)
    result["proxyPassword"] = mask_secret(value.proxy_password)
    return result


async def _model(session: AsyncSession, model_id: int) -> ModelConfig:
    value = await session.get(ModelConfig, model_id)
    if value is None or value.is_deleted:
        raise NotFoundError("Model configuration not found")
    return value


def _same_embedding_space(left: ModelConfig, right: ModelConfig | ModelConfigDTO) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in ("provider", "base_url", "model_name", "embeddings_path")
    )


async def _active_model(session: AsyncSession, model_type: str) -> ModelConfig | None:
    return (
        await session.execute(
            select(ModelConfig)
            .where(
                ModelConfig.model_type == model_type,
                ModelConfig.is_active.is_(True),
                ModelConfig.is_deleted == 0,
            )
            .order_by(ModelConfig.id)
            .limit(1)
        )
    ).scalar_one_or_none()


def _uses_database_embeddings(request: Request) -> bool:
    settings = request.app.state.settings
    return settings.model_mode == "database" and settings.embedding_mode == "database"


async def _switch_active_model(
    request: Request,
    session: AsyncSession,
    value: ModelConfig,
) -> None:
    current = await _active_model(session, value.model_type)
    if (
        value.model_type == "EMBEDDING"
        and _uses_database_embeddings(request)
        and (current is None or (current.id != value.id and not _same_embedding_space(current, value)))
    ):
        # Rebuild with the inactive candidate first. Retrieval replacement is
        # embedding-space scoped, so the current model remains fully queryable
        # until every active schema and recalled knowledge item succeeds.
        await request.app.state.embedding_rebuilder.rebuild(session, value)
    await session.execute(
        update(ModelConfig)
        .where(ModelConfig.model_type == value.model_type, ModelConfig.is_deleted == 0)
        .values(is_active=False)
    )
    value.is_active = True
    await session.commit()


def _candidate_config(value: ModelConfig, changes: dict[str, object]) -> ModelConfig:
    fields = (
        "provider",
        "base_url",
        "api_key",
        "model_name",
        "temperature",
        "is_active",
        "max_tokens",
        "model_type",
        "completions_path",
        "embeddings_path",
        "proxy_enabled",
        "proxy_host",
        "proxy_port",
        "proxy_username",
        "proxy_password",
    )
    data = {field: getattr(value, field) for field in fields}
    data.update(changes)
    return ModelConfig(**data)


@model_router.get("/list")
async def list_models(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ModelConfig).where(ModelConfig.is_deleted == 0).order_by(ModelConfig.id.desc())
        )
    ).scalars()
    return ok([_model_view(item) for item in rows], "获取模型配置列表成功")


@model_router.post("/add")
async def add_model(
    payload: ModelConfigDTO,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    data = payload.model_dump(exclude={"id", "created_time", "updated_time"})
    data["model_type"] = str(data["model_type"]).upper()
    requested_active = bool(data["is_active"])
    # Persist first as inactive. If the candidate rebuild fails, the previous
    # model stays active and this configuration remains available for correction.
    data["is_active"] = False
    value = ModelConfig(**data)
    session.add(value)
    await session.commit()
    await session.refresh(value)
    if requested_active:
        await _switch_active_model(request, session, value)
    return ok(None, "配置已保存")


@model_router.put("/update")
async def update_model(
    payload: ModelConfigDTO,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if payload.id is None:
        raise DataAgentError("id is required")
    value = await _model(session, payload.id)
    requested_type = str(payload.model_type).upper()
    if requested_type != value.model_type:
        raise ConflictError("Model type cannot be changed after creation")
    data = payload.model_dump(exclude={"id", "created_time", "updated_time"})
    data["model_type"] = requested_type
    if str(data.get("api_key", "")).startswith("****"):
        data.pop("api_key")
    if str(data.get("proxy_password", "")).startswith("****"):
        data.pop("proxy_password")
    requested_active = bool(data.get("is_active"))
    was_active = bool(value.is_active)
    candidate = _candidate_config(value, data)
    changed_active_embedding = (
        value.model_type == "EMBEDDING"
        and was_active
        and requested_active
        and not _same_embedding_space(value, candidate)
        and _uses_database_embeddings(request)
    )
    if changed_active_embedding:
        await request.app.state.embedding_rebuilder.rebuild(session, candidate)

    if requested_active and not was_active:
        data["is_active"] = False
    elif requested_active:
        await session.execute(
            update(ModelConfig)
            .where(
                ModelConfig.model_type == value.model_type,
                ModelConfig.is_deleted == 0,
                ModelConfig.id != value.id,
            )
            .values(is_active=False)
        )
    for key, item in data.items():
        setattr(value, key, item)
    await session.commit()
    if requested_active and not was_active:
        await _switch_active_model(request, session, value)
    return ok(None, "配置已更新")


@model_router.post("/activate/{model_id:int}")
async def activate_model(
    model_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    value = await _model(session, model_id)
    await _switch_active_model(request, session, value)
    return ok(None, "模型切换成功！")


@model_router.post("/test/{model_id:int}")
async def test_model(model_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    value = await _model(session, model_id)
    result = await request.app.state.llm.test(session, value)
    if result["success"]:
        return ok(None, "连接测试成功！模型可用。")
    return fail("连接测试失败: " + str(result["message"]), status_code=200)


@model_router.get("/check-ready")
async def model_ready(request: Request, session: AsyncSession = Depends(get_session)):
    return ok(await request.app.state.llm.registry.ready(session), "模型配置检查完成")


@model_router.delete("/{model_id:int}")
async def delete_model(model_id: int, session: AsyncSession = Depends(get_session)):
    value = await _model(session, model_id)
    if value.is_active:
        raise ConflictError("Active model configuration cannot be deleted")
    value.is_deleted = 1
    await session.commit()
    return ok(None, "配置已删除")


PROMPT_TYPES = [
    "report-generator",
    "planner",
    "sql-generator",
    "python-generator",
    "rewrite",
    "general-chat",
]


def _prompt_view(value: UserPromptConfig) -> dict[str, object]:
    result = serialize(value, exclude={"system_prompt"})
    result["optimizationPrompt"] = value.system_prompt
    return result


async def _prompt(session: AsyncSession, config_id: str) -> UserPromptConfig:
    value = await session.get(UserPromptConfig, config_id)
    if value is None:
        raise NotFoundError("Prompt configuration not found")
    return value


@prompt_router.post("/save")
async def save_prompt(payload: PromptConfigDTO, session: AsyncSession = Depends(get_session)):
    config_id = payload.id or str(uuid4())
    value = await session.get(UserPromptConfig, config_id)
    data = payload.model_dump(exclude={"id", "optimization_prompt"})
    data["system_prompt"] = payload.optimization_prompt
    if value is None:
        value = UserPromptConfig(id=config_id, **data)
        session.add(value)
    else:
        for key, item in data.items():
            setattr(value, key, item)
    await session.commit()
    await session.refresh(value)
    return ok(_prompt_view(value), "优化配置保存成功")


@prompt_router.get("/list")
async def list_prompts(session: AsyncSession = Depends(get_session)):
    rows = (
        (
            await session.execute(
                select(UserPromptConfig).order_by(
                    UserPromptConfig.prompt_type,
                    UserPromptConfig.priority.desc(),
                    UserPromptConfig.display_order,
                )
            )
        )
        .scalars()
        .all()
    )
    response = JSONResponse(
        content={
            "success": True,
            "data": [_prompt_view(item) for item in rows],
            "total": len(rows),
        }
    )
    response.headers["X-Total-Count"] = str(len(rows))
    return response


@prompt_router.get("/list-by-type/{prompt_type}")
async def prompts_by_type(
    prompt_type: str,
    agent_id: int | None = Query(None, alias="agentId"),
    session: AsyncSession = Depends(get_session),
):
    statement = select(UserPromptConfig).where(UserPromptConfig.prompt_type == prompt_type)
    if agent_id is not None:
        statement = statement.where(UserPromptConfig.agent_id == agent_id)
    rows = (await session.execute(statement.order_by(UserPromptConfig.priority.desc()))).scalars().all()
    return {"success": True, "data": [_prompt_view(item) for item in rows], "total": len(rows)}


@prompt_router.get("/active/{prompt_type}")
async def active_prompt(
    prompt_type: str,
    agent_id: int | None = Query(None, alias="agentId"),
    session: AsyncSession = Depends(get_session),
):
    statement = select(UserPromptConfig).where(
        UserPromptConfig.prompt_type == prompt_type, UserPromptConfig.enabled.is_(True)
    )
    if agent_id is not None:
        statement = statement.where(UserPromptConfig.agent_id == agent_id)
    value = (
        await session.execute(statement.order_by(UserPromptConfig.priority.desc()).limit(1))
    ).scalar_one_or_none()
    return {
        "success": True,
        "data": _prompt_view(value) if value else None,
        "hasCustomConfig": value is not None,
    }


@prompt_router.get("/active-all/{prompt_type}")
async def all_active_prompts(
    prompt_type: str,
    agent_id: int | None = Query(None, alias="agentId"),
    session: AsyncSession = Depends(get_session),
):
    statement = select(UserPromptConfig).where(
        UserPromptConfig.prompt_type == prompt_type, UserPromptConfig.enabled.is_(True)
    )
    if agent_id is not None:
        statement = statement.where(UserPromptConfig.agent_id == agent_id)
    rows = (await session.execute(statement.order_by(UserPromptConfig.priority.desc()))).scalars().all()
    return {
        "success": True,
        "data": [_prompt_view(item) for item in rows],
        "total": len(rows),
        "hasOptimizationConfigs": bool(rows),
    }


@prompt_router.get("/types")
async def prompt_types():
    return ok(PROMPT_TYPES, "success")


async def _set_enabled(session: AsyncSession, ids: list[str], enabled: bool) -> None:
    await session.execute(
        update(UserPromptConfig).where(UserPromptConfig.id.in_(ids)).values(enabled=enabled)
    )
    await session.commit()


@prompt_router.post("/batch-enable")
async def batch_enable_prompts(ids: list[str], session: AsyncSession = Depends(get_session)):
    await _set_enabled(session, ids, True)
    return ok(None, "批量启用配置成功")


@prompt_router.post("/batch-disable")
async def batch_disable_prompts(ids: list[str], session: AsyncSession = Depends(get_session)):
    await _set_enabled(session, ids, False)
    return ok(None, "批量禁用配置成功")


@prompt_router.get("/{config_id}")
async def get_prompt(config_id: str, session: AsyncSession = Depends(get_session)):
    return ok(_prompt_view(await _prompt(session, config_id)), "success")


@prompt_router.delete("/{config_id}")
async def delete_prompt(config_id: str, session: AsyncSession = Depends(get_session)):
    value = await _prompt(session, config_id)
    await session.delete(value)
    await session.commit()
    return ok(None, "配置删除成功")


@prompt_router.post("/{config_id}/enable")
async def enable_prompt(config_id: str, session: AsyncSession = Depends(get_session)):
    value = await _prompt(session, config_id)
    value.enabled = True
    await session.commit()
    return ok(None, "配置启用成功")


@prompt_router.post("/{config_id}/disable")
async def disable_prompt(config_id: str, session: AsyncSession = Depends(get_session)):
    value = await _prompt(session, config_id)
    value.enabled = False
    await session.commit()
    return ok(None, "配置禁用成功")


@prompt_router.post("/{config_id}/priority")
async def prompt_priority(
    config_id: str, payload: dict[str, int], session: AsyncSession = Depends(get_session)
):
    value = await _prompt(session, config_id)
    value.priority = int(payload.get("priority", 0))
    await session.commit()
    return ok(None, "更新优先级成功")


@prompt_router.post("/{config_id}/display-order")
async def prompt_display_order(
    config_id: str, payload: dict[str, int], session: AsyncSession = Depends(get_session)
):
    value = await _prompt(session, config_id)
    value.display_order = int(payload.get("displayOrder", 0))
    await session.commit()
    return ok(None, "更新显示顺序成功")
