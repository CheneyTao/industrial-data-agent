from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from dataagent.config import ChatGenerationOptions, ChatRequestProfile, ChatTaskBudget
from dataagent.errors import (
    ModelDeadlineError,
    ModelEmptyResponseError,
    ModelOutputBudgetError,
    ModelResponseError,
)
from dataagent.llm import LLMClient
from dataagent.model_calls import capture_model_calls
from dataagent.models import ModelConfig
from dataagent.visualization import ChartSpec


def client(settings, handler):
    settings.model_mode = "database"
    settings.model_retry_backoff_seconds = 0
    llm = LLMClient(settings)
    llm.registry.active = AsyncMock(
        return_value=ModelConfig(
            model_name="primary",
            api_key="secret",
            base_url="https://provider.invalid",
            max_tokens=512,
            temperature=0,
        )
    )
    llm._client = lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm


@pytest.mark.parametrize("content", ["", '{"type":"table"}', '{"type":'])
async def test_length_never_enters_json_repair(settings, content):
    requests, events = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": content}}],
                "usage": {"completion_tokens": 4096, "completion_tokens_details": {"reasoning_tokens": 4096}},
            },
        )

    llm = client(settings, handler)
    with capture_model_calls(events.append), pytest.raises(ModelOutputBudgetError):
        await llm.complete(None, system="sys", user="task", json_model=ChartSpec)
    assert len(requests) == 1
    assert events[0]["finishReason"] == "length"
    assert events[0]["errorType"] == "ModelOutputBudgetError"
    assert events[0]["usage"]["reasoning_tokens"] == 4096


@pytest.mark.parametrize("content", [None, "", "  ", "<think>reasoning only</think>"])
async def test_empty_answer_never_enters_json_repair(settings, content):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with pytest.raises(ModelEmptyResponseError):
        await client(settings, handler).complete(None, system="sys", user="task", json_model=ChartSpec)
    assert len(seen) == 1


async def test_capability_is_endpoint_and_candidate_specific(settings):
    settings.chat_fallback_models = ["backup"]
    settings.chat_request_profiles = [
        ChatRequestProfile(
            endpoint_host="provider.invalid",
            model="primary",
            enable_thinking=False,
        ),
        ChatRequestProfile(endpoint_host="other.invalid", model="backup", enable_thinking=True),
    ]
    settings.chat_task_budgets = {"planner": ChatTaskBudget(max_output_tokens=8192)}
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        )

    assert (
        await client(settings, handler).complete(None, system="sys", user="task", purpose="planner") == "OK"
    )
    assert seen[0]["enable_thinking"] is False and "enable_thinking" not in seen[1]
    assert all(item["max_tokens"] == 8192 for item in seen)
    assert seen[0]["messages"] == seen[1]["messages"]


async def test_repair_failure_preserves_real_error(settings):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "{" if len(seen) == 1 else ""},
                        "finish_reason": "stop" if len(seen) == 1 else "length",
                    }
                ]
            },
        )

    with pytest.raises(ModelOutputBudgetError):
        await client(settings, handler).complete(None, system="sys", user="task", json_model=ChartSpec)
    assert len(seen) == 2


def test_reasoning_policy_is_scoped_to_endpoint_model_and_task(settings):
    settings.chat_request_profiles = [
        ChatRequestProfile(
            endpoint_host="provider.invalid",
            model="primary",
            enable_thinking=False,
            purpose_options={
                "report-review": ChatGenerationOptions(enable_thinking=True, thinking_budget=2048)
            },
        )
    ]
    llm = LLMClient(settings)
    url = "https://provider.invalid/v1/chat/completions"
    assert llm._request_options(url, "primary", "planner") == {"enable_thinking": False}
    assert llm._request_options(url, "primary", "report-review") == {
        "enable_thinking": True,
        "thinking_budget": 2048,
    }
    assert llm._request_options(url, "unknown", "report-review") == {}
    assert llm._request_options("https://other.invalid", "primary", "report-review") == {}
    assert llm._reasoning_reserve(url, "primary", "report-review") == 2048
    assert llm._reasoning_reserve(url, "primary", "planner") == 0


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("code,expected", [("AllocationQuota.FreeTierOnly", 2), ("AccessDenied", 1)])
async def test_only_explicit_model_scoped_403_advances(settings, stream, code, expected):
    settings.chat_fallback_models = ["backup"]
    settings.chat_request_profiles = [
        ChatRequestProfile(
            endpoint_host="provider.invalid",
            model="primary",
            enable_thinking=False,
            unavailable_error_codes=["AllocationQuota.FreeTierOnly"],
        )
    ]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(403, json={"error": {"code": code}})
        return httpx.Response(200)

    llm = client(settings, handler)
    config = await llm.registry.active(None, "CHAT")
    async with llm._request(
        config,
        config.base_url,
        {"model": "primary", "messages": []},
        stream=stream,
        fallback_models=settings.chat_fallback_models,
    ) as response:
        assert response.status_code == (200 if expected == 2 else 403)
    assert len(seen) == expected
    assert all("unavailable_error_codes" not in item for item in seen)


async def test_total_deadline_covers_retries_and_cancel_does_not_fallback(settings):
    settings.chat_completion_timeout_seconds = 0.02
    settings.chat_fallback_models = ["backup"]
    seen = []

    async def handler(request):
        seen.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200)

    with pytest.raises(ModelDeadlineError, match="total budget"):
        await client(settings, handler).complete(None, system="sys", user="task")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "ending,error",
    [
        ('data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n', ModelOutputBudgetError),
        ("", ModelResponseError),
    ],
)
async def test_stream_incomplete_is_not_success_or_replayed(settings, ending, error):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n' + ending)

    received = []
    with pytest.raises(error):
        async for chunk in client(settings, handler).stream(None, system="sys", user="task"):
            received.append(chunk)
    assert received == ["partial"] and len(seen) == 1
