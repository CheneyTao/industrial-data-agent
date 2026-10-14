from __future__ import annotations

import httpx

from dataagent.schemas import Plan


def _payload(
    *,
    name: str,
    model_type: str,
    active: bool,
    api_key: str = "secret-key",
    proxy_password: str = "proxy-secret",
) -> dict[str, object]:
    return {
        "provider": "openai-compatible",
        "baseUrl": "https://models.example.invalid",
        "apiKey": api_key,
        "modelName": name,
        "temperature": 0,
        "isActive": active,
        "maxTokens": 2048,
        "modelType": model_type,
        "completionsPath": "/v1/chat/completions",
        "embeddingsPath": "/v1/embeddings",
        "proxyEnabled": True,
        "proxyHost": "127.0.0.1",
        "proxyPort": 7890,
        "proxyUsername": "proxy-user",
        "proxyPassword": proxy_password,
    }


async def _models(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get("/api/model-config/list")
    assert response.status_code == 200
    return response.json()["data"]


async def test_model_registry_masks_secrets_and_enforces_one_active_per_type(
    client: httpx.AsyncClient,
) -> None:
    first = _payload(name="chat-one", model_type="CHAT", active=True)
    assert (await client.post("/api/model-config/add", json=first)).status_code == 200
    second = _payload(name="chat-two", model_type="CHAT", active=True, api_key="second-key")
    assert (await client.post("/api/model-config/add", json=second)).status_code == 200

    models = await _models(client)
    chats = {item["modelName"]: item for item in models if item["modelType"] == "CHAT"}
    assert chats["chat-one"]["isActive"] is False
    assert chats["chat-two"]["isActive"] is True
    assert chats["chat-two"]["apiKey"].startswith("****")
    assert "second-key" not in str(chats)
    assert chats["chat-two"]["proxyPassword"].startswith("****")


async def test_embedding_hot_switch_preserves_model_management_capability(
    client: httpx.AsyncClient,
) -> None:
    active = _payload(name="embedding-a", model_type="EMBEDDING", active=True)
    inactive = _payload(name="embedding-b", model_type="EMBEDDING", active=False)
    await client.post("/api/model-config/add", json=active)
    await client.post("/api/model-config/add", json=inactive)
    models = await _models(client)
    candidate = next(item for item in models if item["modelName"] == "embedding-b")

    switched = await client.post(f"/api/model-config/activate/{candidate['id']}")
    assert switched.status_code == 200, switched.text
    after = {item["modelName"]: item for item in await _models(client)}
    assert after["embedding-a"]["isActive"] is False
    assert after["embedding-b"]["isActive"] is True


async def test_database_embedding_switch_rebuilds_before_cutover(
    client: httpx.AsyncClient, app
) -> None:
    app.state.settings.model_mode = "database"
    app.state.settings.embedding_mode = "database"
    observations: list[tuple[str, str | None]] = []

    async def rebuild(session, config):
        from sqlalchemy import select

        from dataagent.models import ModelConfig

        active = (
            await session.execute(
                select(ModelConfig).where(
                    ModelConfig.model_type == "EMBEDDING",
                    ModelConfig.is_active.is_(True),
                    ModelConfig.is_deleted == 0,
                )
            )
        ).scalar_one_or_none()
        observations.append((config.model_name, active.model_name if active else None))
        return {}

    app.state.embedding_rebuilder.rebuild = rebuild
    first = _payload(name="embedding-first", model_type="EMBEDDING", active=True)
    second = _payload(name="embedding-second", model_type="EMBEDDING", active=False)
    assert (await client.post("/api/model-config/add", json=first)).status_code == 200
    assert (await client.post("/api/model-config/add", json=second)).status_code == 200
    candidate = next(item for item in await _models(client) if item["modelName"] == "embedding-second")

    response = await client.post(f"/api/model-config/activate/{candidate['id']}")

    assert response.status_code == 200, response.text
    assert observations == [
        ("embedding-first", None),
        ("embedding-second", "embedding-first"),
    ]


async def test_model_update_preserves_masked_secrets_and_rejects_type_change(
    client: httpx.AsyncClient,
) -> None:
    original = _payload(
        name="chat-edit", model_type="CHAT", active=False, api_key="do-not-return", proxy_password="hidden"
    )
    await client.post("/api/model-config/add", json=original)
    model = next(item for item in await _models(client) if item["modelName"] == "chat-edit")
    model["maxTokens"] = 4096
    updated = await client.put("/api/model-config/update", json=model)
    assert updated.status_code == 200, updated.text
    after = next(item for item in await _models(client) if item["id"] == model["id"])
    assert after["maxTokens"] == 4096
    assert after["apiKey"] == model["apiKey"]
    assert after["proxyPassword"] == model["proxyPassword"]

    model["modelType"] = "EMBEDDING"
    rejected = await client.put("/api/model-config/update", json=model)
    assert rejected.status_code == 409
    assert "cannot be changed" in rejected.json()["message"]


async def test_readiness_accepts_explicit_local_bge_embedding(client: httpx.AsyncClient, app) -> None:
    app.state.settings.model_mode = "database"
    app.state.settings.embedding_mode = "local_bge_m3"
    await client.post(
        "/api/model-config/add",
        json=_payload(name="chat-ready", model_type="CHAT", active=True),
    )
    readiness = (await client.get("/api/model-config/check-ready")).json()["data"]
    assert readiness == {
        "ready": True,
        "chatModelReady": True,
        "embeddingModelReady": True,
        "embeddingProvider": "local_bge_m3",
    }


async def test_structured_completion_repairs_invalid_json_and_strips_thinking(app) -> None:
    calls: list[str] = []

    def completion(purpose: str, user: str) -> str:
        calls.append(purpose)
        if purpose == "planner":
            return (
                '<think>private reasoning</think>{"thought_process":"repair" '
                '"execution_plan":[]}'
            )
        if purpose == "json-fix":
            assert "<broken_json>" in user
            assert "private reasoning" not in user
            return '{"thought_process":"repair","execution_plan":[]}'
        raise AssertionError(purpose)

    app.state.llm._mock_completion = completion
    async with app.state.database.session_factory() as session:
        result = await app.state.llm.complete(
            session,
            system="Return a plan",
            user="plan",
            purpose="planner",
            json_model=Plan,
        )

    assert isinstance(result, Plan)
    assert result.thought_process == "repair"
    assert result.execution_plan == []
    assert calls == ["planner", "json-fix"]


async def test_model_connection_activation_and_delete_lifecycle(
    client: httpx.AsyncClient, app
) -> None:
    await client.post(
        "/api/model-config/add",
        json=_payload(name="chat-active", model_type="CHAT", active=True),
    )
    await client.post(
        "/api/model-config/add",
        json=_payload(name="chat-candidate", model_type="CHAT", active=False),
    )
    models = await _models(client)
    active = next(item for item in models if item["modelName"] == "chat-active")
    candidate = next(item for item in models if item["modelName"] == "chat-candidate")

    async def successful_test(_session, _config):
        return {"success": True, "statusCode": 200, "message": "ok"}

    app.state.llm.test = successful_test
    tested = await client.post(f"/api/model-config/test/{candidate['id']}")
    assert tested.status_code == 200
    assert tested.json()["success"] is True

    activated = await client.post(f"/api/model-config/activate/{candidate['id']}")
    assert activated.status_code == 200
    after = {item["id"]: item for item in await _models(client)}
    assert after[candidate["id"]]["isActive"] is True
    assert after[active["id"]]["isActive"] is False

    cannot_delete = await client.delete(f"/api/model-config/{candidate['id']}")
    assert cannot_delete.status_code == 409
    deleted = await client.delete(f"/api/model-config/{active['id']}")
    assert deleted.status_code == 200
    assert active["id"] not in {item["id"] for item in await _models(client)}
