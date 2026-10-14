from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import httpx
from openpyxl import Workbook


async def _agent(client: httpx.AsyncClient, name: str = "Lifecycle Agent") -> dict:
    response = await client.post(
        "/api/agent",
        json={"name": name, "tags": "finance,lifecycle", "status": "draft"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _source_payload(path: Path, name: str) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, value REAL)")
        connection.execute("INSERT INTO facts(value) VALUES (42)")
    return {
        "name": name,
        "type": "sqlite",
        "host": "localhost",
        "port": 0,
        "databaseName": str(path),
        "username": "",
        "password": "database-secret",
        "connectionUrl": f"sqlite:///{path.as_posix()}",
        "status": "inactive",
    }


async def _source(client: httpx.AsyncClient, path: Path, name: str = "Lifecycle source") -> dict:
    response = await client.post("/api/datasource", json=_source_payload(path, name))
    assert response.status_code == 200, response.text
    return response.json()


async def _associate(client: httpx.AsyncClient, agent_id: int, source_id: int) -> None:
    response = await client.post(f"/api/agent/{agent_id}/datasources/{source_id}")
    assert response.status_code == 200, response.text


async def test_agent_key_preset_and_chat_lifecycle(client: httpx.AsyncClient) -> None:
    agent = await _agent(client)
    agent_id = agent["id"]
    assert (await client.get("/api/agent/list", params={"keyword": "finance"})).json()[0][
        "id"
    ] == agent_id

    published = await client.post(f"/api/agent/{agent_id}/publish")
    assert published.json()["status"] == "published"
    offline = await client.post(f"/api/agent/{agent_id}/offline")
    assert offline.json()["status"] == "offline"

    missing_key = await client.post(
        f"/api/agent/{agent_id}/api-key/enable", params={"enabled": True}
    )
    assert missing_key.status_code == 400
    first_key = (
        await client.post(f"/api/agent/{agent_id}/api-key/generate")
    ).json()["data"]["apiKey"]
    reset_key = (
        await client.post(f"/api/agent/{agent_id}/api-key/reset")
    ).json()["data"]["apiKey"]
    assert reset_key != first_key
    assert (
        await client.post(f"/api/agent/{agent_id}/api-key/enable", params={"enabled": False})
    ).status_code == 200
    assert (await client.delete(f"/api/agent/{agent_id}/api-key")).json()["data"] == {
        "apiKey": None,
        "apiKeyEnabled": 0,
    }

    saved = await client.post(
        f"/api/agent/{agent_id}/preset-questions",
        json=[
            {"question": "销售额是多少？", "sortOrder": 2, "isActive": True},
            {"question": "利润率是多少？", "sortOrder": 1, "isActive": False},
        ],
    )
    assert saved.status_code == 200
    presets = (await client.get(f"/api/agent/{agent_id}/preset-questions")).json()
    assert [item["question"] for item in presets] == ["利润率是多少？", "销售额是多少？"]
    assert (
        await client.delete(
            f"/api/agent/{agent_id}/preset-questions/{presets[0]['id']}"
        )
    ).status_code == 200

    first_session = (
        await client.post(f"/api/agent/{agent_id}/sessions", json={"title": "first"})
    ).json()
    second_session = (
        await client.post(f"/api/agent/{agent_id}/sessions", json={"title": "second"})
    ).json()
    await client.post(
        f"/api/sessions/{first_session['id']}/messages",
        json={"role": "user", "content": "hello", "metadata": "{}"},
    )
    assert (
        await client.delete(f"/api/sessions/{first_session['id']}")
    ).status_code == 200
    assert (
        await client.get(f"/api/sessions/{first_session['id']}/messages")
    ).status_code == 404
    assert (await client.delete(f"/api/agent/{agent_id}/sessions")).status_code == 200
    assert (await client.get(f"/api/agent/{agent_id}/sessions")).json() == []
    assert (
        await client.get(f"/api/sessions/{second_session['id']}/messages")
    ).status_code == 404

    assert (await client.delete(f"/api/agent/{agent_id}")).status_code == 200
    assert (await client.get(f"/api/agent/{agent_id}")).status_code == 404


async def test_datasource_relation_and_association_lifecycle(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    agent_id = (await _agent(client, "Datasource Agent"))["id"]
    first = await _source(client, tmp_path / "first.db", "first")
    second = await _source(client, tmp_path / "second.db", "second")
    assert "connectionUrl" not in first

    first_payload = _source_payload(tmp_path / "updated.db", "first-updated")
    first_payload["password"] = first["password"]
    updated = await client.put(f"/api/datasource/{first['id']}", json=first_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "first-updated"
    assert updated.json()["password"] == "****"
    assert "connectionUrl" not in updated.json()
    assert (
        await client.post(f"/api/datasource/{first['id']}/test")
    ).json()["data"] == {"connected": True}

    relation_payload = {
        "sourceTableName": "facts",
        "sourceColumnName": "id",
        "targetTableName": "facts",
        "targetColumnName": "id",
        "relationType": "one-to-one",
        "description": "self relation",
    }
    relation = (
        await client.post(
            f"/api/datasource/{first['id']}/logical-relations", json=relation_payload
        )
    ).json()["data"]
    relation_payload["description"] = "updated relation"
    changed = await client.put(
        f"/api/datasource/{first['id']}/logical-relations/{relation['id']}",
        json=relation_payload,
    )
    assert changed.json()["data"]["description"] == "updated relation"
    duplicate = await client.post(
        f"/api/datasource/{first['id']}/logical-relations", json=relation_payload
    )
    assert duplicate.status_code == 409
    replaced = await client.put(
        f"/api/datasource/{first['id']}/logical-relations",
        json=[relation_payload | {"id": relation["id"], "sourceColumnName": "value"}],
    )
    replacement = replaced.json()["data"][0]
    assert replacement["id"] == relation["id"]
    assert replacement["sourceColumnName"] == "value"
    assert (
        await client.delete(
            f"/api/datasource/{first['id']}/logical-relations/{replacement['id']}"
        )
    ).status_code == 200
    assert (
        await client.get(f"/api/datasource/{first['id']}/logical-relations")
    ).json()["data"] == []

    await _associate(client, agent_id, first["id"])
    await client.post(
        f"/api/agent/{agent_id}/datasources/tables",
        json={"datasourceId": first["id"], "tables": ["facts"]},
    )
    reactivated = await client.post(f"/api/agent/{agent_id}/datasources/{first['id']}")
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["data"]["selectTables"] == ["facts"]
    await _associate(client, agent_id, second["id"])
    conflict = await client.put(
        f"/api/agent/{agent_id}/datasources/toggle",
        json={"datasourceId": first["id"], "isActive": True},
    )
    assert conflict.status_code == 409
    await client.put(
        f"/api/agent/{agent_id}/datasources/toggle",
        json={"datasourceId": second["id"], "isActive": False},
    )
    assert (
        await client.put(
            f"/api/agent/{agent_id}/datasources/toggle",
            json={"datasourceId": first["id"], "isActive": True},
        )
    ).status_code == 200
    active = (await client.get(f"/api/agent/{agent_id}/datasources/active")).json()["data"]
    assert active["datasourceId"] == first["id"]
    assert len((await client.get(f"/api/agent/{agent_id}/datasources")).json()["data"]) == 2
    assert (
        await client.delete(f"/api/agent/{agent_id}/datasources/{second['id']}")
    ).status_code == 200

    assert (await client.delete(f"/api/datasource/{second['id']}")).status_code == 200
    assert (await client.get(f"/api/datasource/{second['id']}")).status_code == 404


async def test_prompt_and_semantic_model_full_lifecycle(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    agent_id = (await _agent(client, "Configuration Agent"))["id"]
    source_id = (await _source(client, tmp_path / "semantic.db", "semantic"))["id"]
    await _associate(client, agent_id, source_id)

    base_prompt = {
        "name": "base",
        "promptType": "planner",
        "agentId": agent_id,
        "optimizationPrompt": "base instructions",
        "enabled": True,
        "priority": 1,
        "displayOrder": 2,
    }
    first_prompt = (await client.post("/api/prompt-config/save", json=base_prompt)).json()[
        "data"
    ]
    second_prompt = (
        await client.post(
            "/api/prompt-config/save",
            json=base_prompt
            | {
                "name": "preferred",
                "optimizationPrompt": "preferred instructions",
                "priority": 10,
            },
        )
    ).json()["data"]
    assert (await client.get("/api/prompt-config/list")).headers["x-total-count"] == "2"
    assert (
        await client.get(
            "/api/prompt-config/list-by-type/planner", params={"agentId": agent_id}
        )
    ).json()["total"] == 2
    active = (
        await client.get("/api/prompt-config/active/planner", params={"agentId": agent_id})
    ).json()
    assert active["data"]["id"] == second_prompt["id"]
    assert (
        await client.get("/api/prompt-config/active-all/planner", params={"agentId": agent_id})
    ).json()["total"] == 2
    await client.post(f"/api/prompt-config/{second_prompt['id']}/disable")
    await client.post(f"/api/prompt-config/{first_prompt['id']}/priority", json={"priority": 5})
    await client.post(
        f"/api/prompt-config/{first_prompt['id']}/display-order",
        json={"displayOrder": 1},
    )
    await client.post("/api/prompt-config/batch-disable", json=[first_prompt["id"]])
    await client.post(
        "/api/prompt-config/batch-enable",
        json=[first_prompt["id"], second_prompt["id"]],
    )
    fetched = (await client.get(f"/api/prompt-config/{first_prompt['id']}")).json()["data"]
    assert fetched["priority"] == 5 and fetched["displayOrder"] == 1 and fetched["enabled"] is True
    assert "planner" in (await client.get("/api/prompt-config/types")).json()["data"]
    assert (await client.delete(f"/api/prompt-config/{second_prompt['id']}")).status_code == 200

    semantic_payload = {
        "agentId": agent_id,
        "datasourceId": source_id,
        "tableName": "facts",
        "columnName": "value",
        "businessName": "指标值",
        "dataType": "REAL",
    }
    assert (await client.post("/api/semantic-model", json=semantic_payload)).status_code == 200
    semantic = (
        await client.get("/api/semantic-model", params={"agentId": agent_id})
    ).json()["data"][0]
    semantic_payload["businessName"] = "更新指标值"
    updated = await client.put(f"/api/semantic-model/{semantic['id']}", json=semantic_payload)
    assert updated.json()["data"]["businessName"] == "更新指标值"
    await client.put("/api/semantic-model/disable", json=[semantic["id"]])
    assert (await client.get(f"/api/semantic-model/{semantic['id']}")).json()["data"][
        "status"
    ] == 0
    await client.put("/api/semantic-model/enable", json=[semantic["id"]])

    batch = await client.post(
        "/api/semantic-model/batch-import",
        json={
            "agentId": agent_id,
            "items": [
                {
                    "tableName": "facts",
                    "columnName": "value",
                    "businessName": "批量更新指标",
                    "dataType": "REAL",
                },
                {
                    "tableName": "facts",
                    "columnName": "id",
                    "businessName": "编号",
                    "dataType": "INTEGER",
                },
            ],
        },
    )
    assert batch.json()["data"]["successCount"] == 2
    assert batch.json()["data"]["failCount"] == 0
    after_batch = (
        await client.get("/api/semantic-model", params={"agentId": agent_id})
    ).json()["data"]
    assert len(after_batch) == 2
    assert next(item for item in after_batch if item["columnName"] == "value")[
        "businessName"
    ] == "批量更新指标"
    invalid_batch = await client.post(
        "/api/semantic-model/batch-import",
        json={
            "agentId": agent_id,
            "items": [{"tableName": "facts", "columnName": "bad", "businessName": "bad"}],
        },
    )
    assert invalid_batch.status_code == 400

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["tableName", "columnName", "businessName", "dataType"])
    sheet.append(["facts", "value", "Excel指标", "REAL"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    imported = await client.post(
        "/api/semantic-model/import/excel",
        data={"agentId": str(agent_id)},
        files={
            "file": (
                "semantic.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert imported.json()["data"]["successCount"] == 1
    template = await client.get("/api/semantic-model/template/download")
    assert template.status_code == 200 and template.content.startswith(b"PK")

    ids = [
        item["id"]
        for item in (
            await client.get("/api/semantic-model", params={"agentId": agent_id})
        ).json()["data"]
    ]
    deleted = await client.request("DELETE", "/api/semantic-model/batch", json=ids)
    assert deleted.status_code == 200
    assert (
        await client.get("/api/semantic-model", params={"agentId": agent_id})
    ).json()["data"] == []


async def test_document_business_knowledge_and_upload_lifecycle(
    client: httpx.AsyncClient,
) -> None:
    agent_id = (await _agent(client, "Knowledge Agent"))["id"]
    created = await client.post(
        "/api/agent-knowledge/create",
        data={
            "agentId": str(agent_id),
            "title": "说明文档",
            "type": "DOCUMENT",
            "isRecall": "false",
            "splitterType": "paragraph",
        },
        files={"file": ("guide.txt", "第一段。\n\n第二段。".encode(), "text/plain")},
    )
    assert created.status_code == 200, created.text
    knowledge_id = created.json()["data"]["id"]
    knowledge = (await client.get(f"/api/agent-knowledge/{knowledge_id}")).json()["data"]
    assert knowledge["sourceFilename"] == "guide.txt"
    assert knowledge["embeddingStatus"] == "COMPLETED"
    assert (
        await client.put(
            f"/api/agent-knowledge/{knowledge_id}", json={"title": "新标题", "content": "忽略"}
        )
    ).json()["data"]["title"] == "新标题"
    assert (
        await client.post(f"/api/agent-knowledge/retry-embedding/{knowledge_id}")
    ).status_code == 400
    assert (
        await client.put(
            f"/api/agent-knowledge/recall/{knowledge_id}", params={"isRecall": True}
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/agent-knowledge/retry-embedding/{knowledge_id}")
    ).status_code == 200

    business = (
        await client.post(
            "/api/business-knowledge",
            json={
                "agentId": agent_id,
                "businessTerm": "GMV",
                "description": "成交总额",
                "isRecall": 0,
            },
        )
    ).json()["data"]
    business_id = business["id"]
    assert (
        await client.post(f"/api/business-knowledge/retry-embedding/{business_id}")
    ).status_code == 400
    await client.post(
        f"/api/business-knowledge/recall/{business_id}", params={"isRecall": True}
    )
    assert (
        await client.put(
            f"/api/business-knowledge/{business_id}",
            json={"businessTerm": "GMV", "description": "更新说明", "synonyms": "成交额"},
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/business-knowledge/retry-embedding/{business_id}")
    ).status_code == 200
    assert (
        await client.post(
            "/api/business-knowledge/refresh-vector-store", params={"agentId": agent_id}
        )
    ).status_code == 200
    assert (await client.get(f"/api/business-knowledge/{business_id}")).json()["data"][
        "description"
    ] == "更新说明"
    assert (await client.delete(f"/api/business-knowledge/{business_id}")).status_code == 200
    assert (await client.delete(f"/api/agent-knowledge/{knowledge_id}")).status_code == 200
    assert (await client.get(f"/api/agent-knowledge/{knowledge_id}")).status_code == 404

    invalid = await client.post(
        "/api/upload/avatar",
        files={"file": ("not-image.txt", b"text", "text/plain")},
    )
    assert invalid.status_code == 400
    avatar = await client.post(
        "/api/upload/avatar",
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert avatar.status_code == 200
    downloaded = await client.get(avatar.json()["url"])
    assert downloaded.status_code == 200 and downloaded.content == b"\x89PNG\r\n\x1a\n"
    traversal = await client.get("/api/upload/%2E%2E/outside.txt")
    assert traversal.status_code in {403, 404}
