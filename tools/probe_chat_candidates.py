"""Bounded candidate checks through the real CHAT client, without changing the registry.

Example (inside the existing service environment)::

    python tools/probe_chat_candidates.py qwen3.7-plus qwen3.6-plus --disable-thinking

Only synthetic arithmetic is sent. Output contains no credentials, prompts, or reasoning.
The probe never enables billing, changes the active model, or tries another provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from dataagent.config import ChatRequestProfile, Settings
from dataagent.database import Database
from dataagent.llm import LLMClient, _join_url
from dataagent.model_calls import capture_model_calls
from dataagent.models import ModelConfig


class ArithmeticResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    count: int
    total: int


class CandidateRegistry:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    async def active(self, _session: object, model_type: str) -> ModelConfig:
        if model_type != "CHAT":
            raise ValueError("The candidate probe only supports CHAT")
        return self.config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", help="Exact model IDs on the configured CHAT endpoint")
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--thinking-only", nargs="*", default=[])
    parser.add_argument("--thinking-budget", type=int, default=1024)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--concurrency", type=int, choices=range(1, 4), default=2)
    args = parser.parse_args()
    if len(args.models) > 16 or any(not name or any(c.isspace() for c in name) for name in args.models):
        parser.error("Use at most 16 exact, whitespace-free model IDs")
    if set(args.thinking_only) - set(args.models):
        parser.error("Every --thinking-only ID must also be an explicitly requested model")
    if not 128 <= args.max_tokens <= 8192 or not 0 <= args.thinking_budget <= 4096:
        parser.error("Probe output must be 128..8192 tokens and thinking budget 0..4096 tokens")
    if not 1 <= args.timeout <= 120:
        parser.error("Probe timeout must be 1..120 seconds per stage")
    return args


async def main(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    llm = LLMClient(settings)
    try:
        async with database.session_factory() as session:
            config = await llm.registry.active(session, "CHAT")
            url = _join_url(config.base_url, config.completions_path, "/v1/chat/completions")
            print(
                json.dumps({"activeModel": config.model_name, "endpointHost": urlparse(url).hostname}),
                flush=True,
            )

            gate = asyncio.Semaphore(args.concurrency)

            async def probe(name: str) -> bool:
                async with gate:
                    # A new transient object is never attached to the session. In particular,
                    # do not mutate the persistent registry object, even temporarily.
                    candidate = ModelConfig(
                        **{
                            column.key: getattr(config, column.key)
                            for column in ModelConfig.__table__.columns
                        }
                    )
                    candidate.model_name = name
                    candidate.max_tokens = args.max_tokens
                    candidate.temperature = 0
                    options = {}
                    if name in args.thinking_only:
                        options = {"enable_thinking": True, "thinking_budget": args.thinking_budget}
                    elif args.disable_thinking:
                        options = {"enable_thinking": False}
                    profile = ChatRequestProfile(
                        endpoint_host=urlparse(url).hostname or "",
                        model=name,
                        unavailable_error_codes=["AllocationQuota.FreeTierOnly"],
                        **options,
                    )
                    probe_settings = settings.model_copy(
                        update={
                            "model_mode": "database",
                            "chat_fallback_models": [],
                            "chat_request_profiles": [profile],
                            "chat_task_budgets": {},
                            "chat_completion_timeout_seconds": args.timeout,
                            "model_http_timeout_seconds": args.timeout,
                            "model_transient_retries": 0,
                            "chat_context_overflow_retries": 0,
                        }
                    )
                    client = LLMClient(probe_settings)
                    client.registry = CandidateRegistry(candidate)
                    result = {"model": name, "options": options, "maxTokens": args.max_tokens, "stages": {}}
                    for stage in ("structured", "stream"):
                        started = time.monotonic()
                        calls = []
                        details = {}
                        try:
                            with capture_model_calls(calls.append):
                                if stage == "structured":
                                    answer = await client.complete(
                                        session,
                                        system="Return only JSON with integer fields count and total.",
                                        user="Count and sum the integers 1 through 10 inclusive.",
                                        purpose="candidate-probe",
                                        json_model=ArithmeticResult,
                                    )
                                    details["passed"] = answer == ArithmeticResult(count=10, total=55)
                                else:
                                    chunks = [
                                        chunk
                                        async for chunk in client.stream(
                                            session,
                                            system="Return only the numerical answer, without explanation.",
                                            user="What is 1 + 1?",
                                            purpose="candidate-probe",
                                        )
                                    ]
                                    details["passed"] = "".join(chunks).strip() == "2"
                                    details["visibleChunks"] = len(chunks)
                        except Exception as exc:
                            # Provider messages can contain account data: emit the type and
                            # the existing client's safe call metadata, never the raw error.
                            details.update(passed=False, errorType=type(exc).__name__)
                        details.update(elapsedSeconds=round(time.monotonic() - started, 3), calls=calls)
                        result["stages"][stage] = details
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    return all(stage["passed"] for stage in result["stages"].values())

            results = await asyncio.gather(*(probe(name) for name in dict.fromkeys(args.models)))
            return 0 if all(results) else 1
    finally:
        await database.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
