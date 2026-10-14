from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from dataagent.llm import LLMClient
from dataagent.models import ModelConfig
from dataagent.visualization import ChartSpec


def configured_client(settings, handler):
    settings.model_transient_retries = 2
    settings.model_retry_backoff_seconds = 0
    llm = LLMClient(settings)
    config = ModelConfig(api_key="test-only", model_name="test", base_url="http://model.invalid")
    llm._client = lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm, config


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_transient_status_retries_same_payload(settings, stream, status):
    requests = []

    def handler(request):
        requests.append(request.content)
        return httpx.Response(status if len(requests) == 1 else 200, text="ok")

    llm, config = configured_client(settings, handler)
    async with llm._request(config, config.base_url, {"input": "unchanged"}, stream=stream) as response:
        assert response.status_code == 200
    assert requests == [b'{"input":"unchanged"}'] * 2


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_invalid_request_and_credentials_are_not_retried(settings, stream, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    llm, config = configured_client(settings, handler)
    async with llm._request(config, config.base_url, {}, stream=stream) as response:
        assert response.status_code == status
    assert len(requests) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_transient_retry_budget_is_bounded(settings, stream):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503)

    llm, config = configured_client(settings, handler)
    async with llm._request(config, config.base_url, {}, stream=stream) as response:
        assert response.status_code == 503
    assert len(requests) == 3


@pytest.mark.parametrize("stream", [False, True])
async def test_connection_error_before_response_can_retry(settings, stream):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200)

    llm, config = configured_client(settings, handler)
    async with llm._request(config, config.base_url, {}, stream=stream) as response:
        assert response.status_code == 200
    assert len(requests) == 2


async def test_stream_is_never_replayed_after_response_is_handed_off(settings):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first chunk"
            raise httpx.ReadError("connection interrupted after partial output")

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=BrokenStream())

    llm, config = configured_client(settings, handler)
    chunks = []
    with pytest.raises(httpx.ReadError):
        async with llm._request(config, config.base_url, {}, stream=True) as response:
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
    assert chunks == [b"first chunk"]
    assert len(requests) == 1


@pytest.mark.parametrize("schema_error", [False, True])
async def test_structural_regeneration_is_distinct_from_syntax_repair(settings, schema_error):
    import json

    settings.model_mode = "database"
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        invalid = '{"type":"object","properties":{}}' if schema_error else '{"type":"bar",'
        valid = '{"type":"bar","x":"model_name","y":["oee"]}'
        return httpx.Response(
            200, json={"choices": [{"message": {"content": invalid if len(requests) == 1 else valid}}]}
        )

    llm, config = configured_client(settings, handler)
    config.max_tokens, config.temperature = 256, 0
    llm.registry.active = AsyncMock(return_value=config)
    result = await llm.complete(
        object(), system="Choose real fields only", user="Compare 2015 OEE", json_model=ChartSpec
    )
    assert result.type == "bar"
    assert len(requests) == 2
    retry = requests[1]["messages"]
    if schema_error:
        assert "Compare 2015 OEE" in retry[1]["content"]
        assert "JSON Schema" in retry[1]["content"]
        assert "Choose real fields only" in retry[0]["content"]
    else:
        assert "Repair JSON syntax only" in retry[0]["content"]
