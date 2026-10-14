from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from dataagent.api import (
    agent_datasources,
    agents,
    attachments,
    bi,
    chats,
    configuration,
    dashboard,
    datasources,
    diagnostics,
    graph,
    knowledge,
    report_operations,
    semantic,
    uploads,
)
from dataagent.config import Settings, get_settings
from dataagent.connectors import ConnectorService
from dataagent.dashboard import DashboardService
from dataagent.database import Database, get_database
from dataagent.documents import DocumentExtractor, KnowledgeIndexer, TextSplitter, get_file_storage
from dataagent.embedding_rebuild import EmbeddingRebuildService
from dataagent.errors import DataAgentError
from dataagent.llm import LLMClient
from dataagent.maintenance import KnowledgeMaintenance
from dataagent.memory_cache import RedisMemoryCache
from dataagent.report_operations import ReportOperations
from dataagent.schema_index import SchemaIndexer
from dataagent.session_events import SessionEventBroker
from dataagent.telemetry import Telemetry
from dataagent.vectorstore import RetrievalService, create_vector_store
from dataagent.version import VERSION, build_identity
from dataagent.workflow.engine import GraphEngine


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    config = settings or get_settings()
    db = database or (Database(config) if settings is not None else get_database())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.create_schema()
        await app.state.memory_cache.start()
        maintenance = KnowledgeMaintenance(app)
        app.state.maintenance = maintenance
        maintenance.start()
        await app.state.report_operations.start()
        try:
            yield
        finally:
            await maintenance.close()
            await app.state.report_operations.close()
            await app.state.graph.close()
            await app.state.dashboard.close()
            await app.state.telemetry.close()
            vector_close = getattr(app.state.vector_store, "close", None)
            if vector_close is not None:
                await vector_close()
            await app.state.memory_cache.close()
            await db.close()

    app = FastAPI(
        title="Spring AI Alibaba DataAgent - Python",
        version=VERSION,
        description="Behavior-compatible Python reimplementation of the audited DataAgent main branch.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.database = db
    app.state.telemetry = Telemetry(config)
    app.state.llm = LLMClient(config, app.state.telemetry)
    app.state.vector_store = create_vector_store(config)
    app.state.retrieval = RetrievalService(config, app.state.llm, app.state.vector_store)
    app.state.connectors = ConnectorService(config)
    app.state.dashboard = DashboardService(config)
    app.state.file_storage = get_file_storage(config)
    app.state.document_extractor = DocumentExtractor(config)
    app.state.text_splitter = TextSplitter(config, app.state.llm)
    app.state.knowledge_indexer = KnowledgeIndexer(app.state.retrieval, app.state.text_splitter)
    app.state.schema_indexer = SchemaIndexer(app.state.connectors, app.state.retrieval)
    app.state.embedding_rebuilder = EmbeddingRebuildService(app)
    app.state.session_events = SessionEventBroker()
    app.state.memory_cache = RedisMemoryCache(config)
    app.state.graph = GraphEngine(
        config,
        db,
        app.state.llm,
        app.state.retrieval,
        app.state.connectors,
        telemetry=app.state.telemetry,
        memory_cache=app.state.memory_cache,
        dashboard=app.state.dashboard,
    )
    app.state.report_operations = ReportOperations(
        db,
        app.state.graph,
        enabled=config.report_scheduler_enabled,
        poll_interval_seconds=config.report_scheduler_poll_seconds,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for api_router in (
        agents.router,
        agent_datasources.router,
        knowledge.agent_router,
        knowledge.business_router,
        chats.router,
        chats.preset_router,
        dashboard.router,
        bi.router,
        diagnostics.router,
        report_operations.router,
        datasources.router,
        graph.router,
        configuration.model_router,
        configuration.prompt_router,
        semantic.router,
        uploads.router,
    ):
        app.include_router(api_router)
    app.include_router(attachments.router)

    @app.exception_handler(DataAgentError)
    async def domain_error(_: Request, exc: DataAgentError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "message": exc.message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for issue in exc.errors():
            location = ".".join(str(part) for part in issue.get("loc", ()) if part != "body")
            prefix = f"{location}: " if location else ""
            details.append(prefix + str(issue.get("msg", "invalid request")))
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "; ".join(details) or "Invalid request",
                "data": None,
            },
        )

    @app.get("/echo/ok", tags=["system"])
    async def echo_ok() -> str:
        return "ok"

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict[str, Any]:
        management: dict[str, Any]
        try:
            async with request.app.state.database.session_factory() as session:
                await session.execute(text("SELECT 1"))
            management = {"ready": True}
        except Exception as exc:
            management = {"ready": False, "error": str(exc)}
        sandbox = await request.app.state.graph.sandbox.health()
        redis = await request.app.state.memory_cache.health()
        return {
            "build": build_identity(),
            "status": (
                "ready" if management["ready"] and sandbox["ready"] and redis["ready"] else "degraded"
            ),
            "management": management,
            "redis": redis,
            "sandbox": sandbox,
        }

    return app
