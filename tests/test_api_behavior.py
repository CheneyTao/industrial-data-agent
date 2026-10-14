from __future__ import annotations

import re
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI

from dataagent.schemas import GraphNodeResponse


async def _create_agent(client: httpx.AsyncClient, *, human_review: bool = False) -> dict:
    response = await client.post(
        "/api/agent",
        json={
            "name": "Sales Agent",
            "description": "Revenue analysis",
            "status": "draft",
            "prompt": "Always explain monetary units.",
            "tags": "sales,finance",
            "humanReviewEnabled": human_review,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class _CompletedGraph:
    async def stream(self, request) -> AsyncIterator[tuple[str | None, GraphNodeResponse]]:
        yield (
            "complete",
            GraphNodeResponse(
                agent_id=request.agent_id,
                thread_id="test-run",
                complete=True,
            ),
        )


async def test_agent_api_key_sse_and_chat_lifecycle(client: httpx.AsyncClient, app: FastAPI) -> None:
    agent = await _create_agent(client, human_review=True)
    agent_id = agent["id"]
    assert agent["humanReviewEnabled"] == 1

    updated = (
        await client.put(
            f"/api/agent/{agent_id}",
            json={"name": "renamed", "description": "only this changed"},
        )
    ).json()
    assert updated["name"] == "renamed"
    assert updated["description"] == "only this changed"
    assert updated["prompt"] == "Always explain monetary units."
    assert updated["humanReviewEnabled"] == 1
    assert agent["apiKey"] is None

    listed = (await client.get("/api/agent/list", params={"keyword": "renamed"})).json()
    assert [item["id"] for item in listed] == [agent_id]

    generated = (await client.post(f"/api/agent/{agent_id}/api-key/generate")).json()
    plain = generated["data"]["apiKey"]
    assert re.fullmatch(r"sk-[A-Za-z0-9]{32}", plain)
    masked = (await client.get(f"/api/agent/{agent_id}/api-key")).json()["data"]["apiKey"]
    assert masked == "****" + plain[-4:]

    app.state.graph = _CompletedGraph()
    params = {"agentId": agent_id, "query": "sales", "conversationId": "conversation-1"}
    assert (await client.get("/api/stream/search", params=params)).status_code == 401
    response = await client.get("/api/stream/search", params=params, headers={"X-API-Key": plain})
    assert response.status_code == 200
    assert "event: complete" in response.text
    assert '"complete": true' in response.text

    chat = (await client.post(f"/api/agent/{agent_id}/sessions", json={"title": "Q3"})).json()
    session_id = chat["id"]
    message = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"role": "user", "content": "Q3 revenue", "messageType": "text"},
    )
    assert message.json()["content"] == "Q3 revenue"
    assert len((await client.get(f"/api/sessions/{session_id}/messages")).json()) == 1
    assert (await client.put(f"/api/sessions/{session_id}/pin", params={"isPinned": True})).json()["success"]
    await client.put(f"/api/sessions/{session_id}/rename", params={"title": "Quarter 3"})
    sessions = (await client.get(f"/api/agent/{agent_id}/sessions")).json()
    assert sessions[0]["title"] == "Quarter 3"

    report = await client.post(
        f"/api/sessions/{session_id}/reports/html",
        content="# Report",
        headers={"Content-Type": "text/plain"},
    )
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/html")
    assert "DataAgent Analysis Report" in report.text


async def test_datasource_association_knowledge_prompt_and_semantic_apis(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    agent_id = (await _create_agent(client))["id"]
    analysis_path = tmp_path / "source.db"
    with sqlite3.connect(analysis_path) as connection:
        connection.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, amount REAL)")

    source_response = await client.post(
        "/api/datasource",
        json={
            "name": "Local analytics",
            "type": "sqlite",
            "host": "localhost",
            "port": 0,
            "databaseName": str(analysis_path),
            "username": "",
            "password": "secret",
            "connectionUrl": f"sqlite:///{analysis_path.as_posix()}",
            "status": "active",
        },
    )
    assert source_response.status_code == 200, source_response.text
    source = source_response.json()
    source_id = source["id"]
    assert source["password"] == "****"
    assert (await client.get(f"/api/datasource/{source_id}/tables")).json() == ["sales"]
    columns = (await client.get(f"/api/datasource/{source_id}/tables/sales/columns")).json()
    assert columns["data"] == ["id", "amount"]

    association = await client.post(f"/api/agent/{agent_id}/datasources/{source_id}")
    assert association.status_code == 200, association.text
    await client.put(
        f"/api/agent/{agent_id}/datasources/toggle",
        json={"datasourceId": source_id, "isActive": True},
    )
    selected = await client.post(
        f"/api/agent/{agent_id}/datasources/tables",
        json={"datasourceId": source_id, "tables": ["sales"]},
    )
    assert selected.json()["success"]
    initialized = await client.post(f"/api/agent/{agent_id}/datasources/init")
    assert initialized.json()["data"] == {"tableCount": 1, "columnCount": 2}

    knowledge = await client.post(
        "/api/agent-knowledge/create",
        json={
            "agentId": agent_id,
            "title": "Metric guide",
            "type": "QA",
            "question": "What is revenue?",
            "content": "Revenue is amount.",
            "isRecall": 0,
        },
    )
    assert knowledge.status_code == 200, knowledge.text
    knowledge_id = knowledge.json()["data"]["id"]
    statistics = (await client.get(f"/api/agent-knowledge/statistics/{agent_id}")).json()
    assert statistics["data"]["total"] == 1
    page = await client.post(
        "/api/agent-knowledge/query/page",
        json={"agentId": agent_id, "pageNum": 1, "pageSize": 10},
    )
    assert page.json()["total"] == 1
    assert (await client.delete(f"/api/agent-knowledge/{knowledge_id}")).json()["success"]

    business = await client.post(
        "/api/business-knowledge",
        json={
            "agentId": agent_id,
            "businessTerm": "GMV",
            "description": "Gross merchandise value",
            "isRecall": 0,
        },
    )
    assert business.status_code == 200, business.text
    business_id = business.json()["data"]["id"]
    assert (
        len((await client.get("/api/business-knowledge", params={"agentId": agent_id})).json()["data"]) == 1
    )
    await client.delete(f"/api/business-knowledge/{business_id}")

    semantic = await client.post(
        "/api/semantic-model",
        json={
            "agentId": agent_id,
            "datasourceId": source_id,
            "tableName": "sales",
            "columnName": "amount",
            "businessName": "Revenue",
            "dataType": "REAL",
        },
    )
    assert semantic.status_code == 200, semantic.text
    assert semantic.json()["data"] is True
    models = (await client.get("/api/semantic-model", params={"agentId": agent_id})).json()
    assert models["data"][0]["businessName"] == "Revenue"

    prompt = await client.post(
        "/api/prompt-config/save",
        json={
            "name": "Sales SQL policy",
            "promptType": "sql-generator",
            "agentId": agent_id,
            "optimizationPrompt": "Always include currency units.",
            "enabled": True,
            "priority": 10,
        },
    )
    assert prompt.status_code == 200, prompt.text
    prompt_id = prompt.json()["data"]["id"]
    active = (
        await client.get("/api/prompt-config/active/sql-generator", params={"agentId": agent_id})
    ).json()
    assert active["data"]["id"] == prompt_id

    type_codes = {item["code"] for item in (await client.get("/api/datasource/types")).json()["data"]}
    assert type_codes == {"mysql", "postgresql", "h2", "dameng", "sqlserver", "oracle", "hive"}
