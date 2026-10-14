from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from dataagent.config import Settings
from dataagent.errors import DataAgentError
from dataagent.llm import LLMClient
from dataagent.model_calls import capture_model_calls, record_model_call
from dataagent.models import Agent, ModelConfig
from dataagent.schemas import GraphRequest
from dataagent.workflow.nodes import NodeResult
from dataagent.workflow.state import END, FINAL_ANSWER, INTENT_RECOGNITION_NODE


def setup_client(settings, handler):
    settings.model_mode = "database"
    settings.model_retry_backoff_seconds = 0
    settings.chat_fallback_models = ["backup-a", "backup-b"]
    llm = LLMClient(settings)
    config = ModelConfig(
        model_name="primary",
        api_key="test-secret",
        base_url="https://provider.invalid",
        max_tokens=256,
        temperature=0,
    )
    llm.registry.active = AsyncMock(return_value=config)
    llm._client = lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm, config


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 404])
async def test_ordered_fallback_preserves_endpoint_credentials_and_task(settings, stream, status):
    seen, calls = [], []

    def handler(request):
        seen.append(request)
        return httpx.Response(status if len(seen) < 3 else 200)

    llm, config = setup_client(settings, handler)
    payload = {"model": "primary", "messages": [{"role": "user", "content": "same question"}]}
    with capture_model_calls(calls.append):
        async with llm._request(
            config,
            config.base_url,
            payload,
            stream=stream,
            fallback_models=settings.chat_fallback_models,
            purpose="planner",
        ) as response:
            assert response.status_code == 200
    assert [json.loads(r.content)["model"] for r in seen] == ["primary", "backup-a", "backup-b"]
    assert all(json.loads(r.content)["messages"] == payload["messages"] for r in seen)
    assert all(str(r.url).startswith(config.base_url) for r in seen)
    assert all(r.headers["Authorization"] == "Bearer test-secret" for r in seen)
    assert config.model_name == payload["model"] == "primary"
    assert [c["outcome"] for c in calls] == ["retry", "retry", "success"]
    assert [c["fallback"] for c in calls] == [False, True, True]
    assert len({c["callId"] for c in calls}) == 1
    assert "test-secret" not in json.dumps(calls)
    assert "same question" not in json.dumps(calls)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_configuration_errors_do_not_switch_models(settings, stream, status):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status)

    llm, config = setup_client(settings, handler)
    async with llm._request(
        config,
        config.base_url,
        {"model": "primary"},
        stream=stream,
        fallback_models=settings.chat_fallback_models,
    ) as response:
        assert response.status_code == status
    assert len(seen) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_public_chat_uses_fallback_after_timeout(settings, stream):
    seen, calls = [], []

    def handler(request):
        seen.append(json.loads(request.content)["model"])
        if len(seen) == 1:
            raise httpx.ReadTimeout("", request=request)
        if stream:
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    llm, config = setup_client(settings, handler)
    with capture_model_calls(calls.append):
        if stream:
            result = "".join([chunk async for chunk in llm.stream(object(), system="sys", user="user")])
        else:
            result = await llm.complete(object(), system="sys", user="user")
    assert result == "OK"
    assert seen == ["primary", "backup-a"]
    assert calls[0]["errorType"] == "ReadTimeout"
    assert calls[0]["nextModel"] == "backup-a"
    assert config.model_name == "primary"


async def test_all_unavailable_stops_at_shared_budget(settings):
    calls = []

    def handler(request):
        raise httpx.ReadTimeout("", request=request)

    llm, _ = setup_client(settings, handler)
    with capture_model_calls(calls.append), pytest.raises(httpx.ReadTimeout):
        await llm.complete(object(), system="sys", user="user")
    assert [c["model"] for c in calls] == ["primary", "backup-a", "backup-b"]
    assert calls[-1]["outcome"] == "failed"


async def test_all_http_unavailable_stops_at_shared_budget(settings):
    calls = []
    llm, _ = setup_client(settings, lambda _: httpx.Response(503))
    with capture_model_calls(calls.append), pytest.raises(DataAgentError):
        await llm.complete(object(), system="sys", user="user")
    assert len(calls) == 3
    assert calls[-1]["outcome"] == "http_error"


async def test_embeddings_never_use_chat_fallback(settings):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["model"])
        if len(seen) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 16}]})

    llm, config = setup_client(settings, handler)
    await llm.embed_bundle_for_config(config, ["text"])
    assert seen == ["primary", "primary"]


async def test_started_stream_never_falls_back(settings):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError("interrupted")

    seen, calls, chunks = [], [], []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=BrokenStream())

    llm, _ = setup_client(settings, handler)
    with capture_model_calls(calls.append), pytest.raises(httpx.ReadError):
        async for chunk in llm.stream(object(), system="sys", user="user"):
            chunks.append(chunk)
    assert chunks == ["partial"]
    assert len(seen) == 1
    assert calls[0]["outcome"] == "failed"


async def test_success_never_calls_more_expensive_backup(settings):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    llm, _ = setup_client(settings, handler)
    assert await llm.complete(object(), system="sys", user="user") == "OK"
    assert len(seen) == 1


async def test_trace_context_isolated_and_reset_after_error():
    a, b = [], []

    async def run(events, name):
        with capture_model_calls(events.append):
            await asyncio.sleep(0)
            record_model_call({"model": name})

    await asyncio.gather(run(a, "a"), run(b, "b"))
    assert a == [{"model": "a"}] and b == [{"model": "b"}]
    with pytest.raises(ValueError), capture_model_calls(a.append):
        raise ValueError("test")
    record_model_call({"model": "outside"})
    assert a == [{"model": "a"}]


@pytest.mark.parametrize("invalid", [[""], ["a b"], ["\n"], [str(i) for i in range(9)]])
def test_fallback_configuration_validation(invalid):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_fallback_models=invalid)
    assert Settings(_env_file=None, chat_fallback_models=[" a ", "a", "b"]).chat_fallback_models == ["a", "b"]


@pytest.mark.parametrize("fail", [False, True])
async def test_graph_persists_model_attempts_without_langfuse(app, fail):
    async with app.state.database.session_factory() as session:
        agent = Agent(name="Fallback test")
        session.add(agent)
        await session.commit()
        agent_id = agent.id

    def handler(request):
        model = json.loads(request.content)["model"]
        if fail or model == "primary":
            raise httpx.ReadTimeout("", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    llm, _ = setup_client(app.state.settings, handler)

    class Node:
        async def run(self, context):
            context.state[FINAL_ANSWER] = await llm.complete(context.session, system="sys", user="question")
            return NodeResult(END)

    app.state.graph.nodes[INTENT_RECOGNITION_NODE] = Node()
    events = [e async for _, e in app.state.graph.stream(GraphRequest(agent_id=str(agent_id), query="test"))]
    assert not app.state.settings.langfuse_enabled
    run_id = events[-1].thread_id
    store = app.state.graph.artifacts
    trace = store.load(run_id, agent_id)
    calls = [
        store.get(run_id, agent_id, a["artifactId"]) for a in trace["artifacts"] if a["kind"] == "model_call"
    ]
    assert len(calls) == (3 if fail else 2)
    assert trace["status"] == ("failed" if fail else "succeeded")
    output_ids = trace["steps"][0]["outputArtifactIds"]
    assert all(a["artifactId"] in output_ids for a in calls)
    if fail:
        error = next(a for a in trace["artifacts"] if a["kind"] == "node_error")
        content = store.get(run_id, agent_id, error["artifactId"])["content"]
        assert content["errorType"] == content["message"] == "ReadTimeout"
        assert any(e.error and "ReadTimeout" in e.text for e in events)
