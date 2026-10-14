"""Isolated UI acceptance server, never a substitute for production model tests.

Uses real uploads, parsers, SQLite SQL and graph; ONLY the model is deterministic.
Run the frontend on port 13006 with NUXT_BACKEND_URL=http://127.0.0.1:18076.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import uvicorn

from dataagent.app import create_app
from dataagent.config import Settings
from dataagent.database import Database
from dataagent.models import Agent

root = Path(__file__).resolve().parents[1] / "work" / "attachment-smoke"
root.mkdir(parents=True, exist_ok=True)
settings = Settings(
    _env_file=None,
    management_database_url=f"sqlite+aiosqlite:///{(root / 'management.sqlite').as_posix()}",
    model_mode="mock",
    vector_file_path=root / "vectors.json",
    vector_embedding_dimension=16,
    file_path=root / "avatars",
    redis_url="",
    redis_required=False,
)
settings.ensure_directories()
database = Database(settings)


async def seed():
    await database.create_schema()
    async with database.session_factory() as session:
        if await session.get(Agent, 1) is None:
            session.add(
                Agent(
                    id=1,
                    name="附件功能验收（隔离测试）",
                    description="真实上传/解析/SQL；模型为测试替身，不是实际 AI 联调",
                    status="published",
                )
            )
            await session.commit()


async def main():
    await seed()
    server = uvicorn.Server(uvicorn.Config(create_app(settings, database), host="127.0.0.1", port=18076))
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
