from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from dataagent.app import create_app
from dataagent.config import Settings
from dataagent.database import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    config = Settings(
        _env_file=None,
        management_database_url=f"sqlite+aiosqlite:///{(tmp_path / 'management.db').as_posix()}",
        model_mode="mock",
        vector_file_path=tmp_path / "vectors.json",
        vector_embedding_dimension=16,
        vector_default_threshold=0.0,
        vector_table_threshold=-1.0,
        file_path=tmp_path / "uploads",
        text_chunk_size=40,
        text_min_chunk_chars=1,
        text_chunk_overlap=5,
        text_paragraph_overlap_chars=5,
    )
    config.ensure_directories()
    return config


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    database = Database(settings)
    await database.create_schema()
    application = create_app(settings, database)
    yield application
    await application.state.telemetry.close()
    await database.close()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value
