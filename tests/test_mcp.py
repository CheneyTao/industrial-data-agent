from __future__ import annotations

from fastapi import FastAPI
from mcp import Client

import dataagent.mcp_server as mcp_module
from dataagent.models import Agent


class _GraphStub:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def nl2sql(self, natural_query: str, agent_id: str) -> str:
        return f"SELECT '{agent_id}' AS agent_id, '{natural_query}' AS query"


async def test_mcp_v2_tools_register_and_execute(monkeypatch, app: FastAPI) -> None:
    async with app.state.database.session_factory() as session:
        session.add(Agent(name="MCP Agent", description="tool test", status="published"))
        await session.commit()

    monkeypatch.setattr(mcp_module, "get_settings", lambda: app.state.settings)
    monkeypatch.setattr(mcp_module, "get_database", lambda: app.state.database)
    monkeypatch.setattr(mcp_module, "GraphEngine", _GraphStub)
    server = mcp_module.create_mcp_server()

    async with Client(server) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "listAgentsToolCallback",
            "nl2SqlToolCallback",
            "begin_analysis",
            "get_schema",
            "execute_query",
            "create_chart",
            "analyze_result",
            "calculate_result",
            "get_run_trace",
            "get_artifact",
            "generate_report",
            "finish_analysis",
            "analyze_request",
            "list_bi_datasets",
            "get_bi_catalog",
            "analyze_bi",
            "get_bi_task",
            "cancel_bi_task",
        }

        listed = await client.call_tool("listAgentsToolCallback", {"status": "published", "keyword": "MCP"})
        assert not listed.is_error
        assert "MCP Agent" in str(listed.content)

        generated = await client.call_tool(
            "nl2SqlToolCallback",
            {"naturalQuery": "本月收入", "agentId": "1"},
        )
        assert not generated.is_error
        assert "SELECT" in str(generated.content)
        assert "本月收入" in str(generated.content)
