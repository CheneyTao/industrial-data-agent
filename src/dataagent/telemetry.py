from __future__ import annotations

import asyncio
import base64
import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from dataagent.config import Settings, get_settings
from dataagent.errors import ConfigurationError


@dataclass(slots=True)
class TraceHandle:
    span: Any = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Telemetry:
    """Optional Langfuse OTLP tracing with graph, node-attempt and token spans."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.enabled = self.settings.langfuse_enabled
        self.provider: Any = None
        self.tracer: Any = None
        self._root: ContextVar[TraceHandle | None] = ContextVar("dataagent_trace_root", default=None)
        self._node: ContextVar[TraceHandle | None] = ContextVar("dataagent_trace_node", default=None)
        if self.enabled:
            self._configure()

    def _configure(self) -> None:
        if not all(
            (
                self.settings.langfuse_host,
                self.settings.langfuse_public_key,
                self.settings.langfuse_secret_key,
            )
        ):
            raise ConfigurationError(
                "Langfuse host, public key and secret key are required when tracing is enabled"
            )
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:
            raise ConfigurationError("Install the 'observability' extra to enable Langfuse tracing") from exc

        credentials = (f"{self.settings.langfuse_public_key}:{self.settings.langfuse_secret_key}").encode()
        authorization = base64.b64encode(credentials).decode("ascii")
        endpoint = self.settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces"
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={"Authorization": f"Basic {authorization}"},
            timeout=10,
        )
        self.provider = TracerProvider(resource=Resource.create({"service.name": "data-agent"}))
        self.provider.add_span_processor(
            BatchSpanProcessor(exporter, schedule_delay_millis=1000, max_export_batch_size=100)
        )
        self.tracer = self.provider.get_tracer("data-agent")

    @staticmethod
    def _render(value: Any) -> str:
        try:
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            rendered = str(value)
        return rendered[:8000]

    def start_graph(
        self,
        *,
        name: str,
        agent_id: str,
        thread_id: str,
        query: str,
        nl2sql_only: bool,
        human_feedback: bool,
    ) -> tuple[TraceHandle, Token[TraceHandle | None] | None]:
        if not self.enabled:
            return TraceHandle(), None
        from opentelemetry.trace import SpanKind

        span = self.tracer.start_span(name, kind=SpanKind.CLIENT)
        span.set_attribute(
            "input.value",
            self._render(
                {
                    "query": query,
                    "agentId": agent_id,
                    "threadId": thread_id,
                    "nl2sqlOnly": nl2sql_only,
                    "humanFeedback": human_feedback,
                }
            ),
        )
        span.set_attribute("data_agent.agent_id", agent_id)
        span.set_attribute("data_agent.thread_id", thread_id)
        span.set_attribute("data_agent.nl2sql_only", nl2sql_only)
        span.set_attribute("data_agent.human_feedback", human_feedback)
        handle = TraceHandle(span=span)
        return handle, self._root.set(handle)

    def start_node(
        self, node_name: str, attempt: int, state: dict[str, Any]
    ) -> tuple[TraceHandle, Token[TraceHandle | None] | None]:
        root = self._root.get()
        if not self.enabled or root is None or root.span is None:
            return TraceHandle(), None
        from opentelemetry.trace import SpanKind, set_span_in_context

        span = self.tracer.start_span(
            f"{node_name}#{attempt}",
            context=set_span_in_context(root.span),
            kind=SpanKind.INTERNAL,
        )
        span.set_attribute("langfuse.observation.type", "generation")
        span.set_attribute("data_agent.node", node_name)
        span.set_attribute("data_agent.attempt", attempt)
        span.set_attribute("input.value", self._render(state))
        handle = TraceHandle(span=span)
        return handle, self._node.set(handle)

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        for handle in (self._root.get(), self._node.get()):
            if handle is not None:
                handle.prompt_tokens += prompt
                handle.completion_tokens += completion

    @staticmethod
    def _usage_attributes(handle: TraceHandle) -> None:
        if handle.span is None:
            return
        total = handle.prompt_tokens + handle.completion_tokens
        if total:
            handle.span.set_attribute("gen_ai.usage.prompt_tokens", handle.prompt_tokens)
            handle.span.set_attribute("gen_ai.usage.completion_tokens", handle.completion_tokens)
            handle.span.set_attribute("gen_ai.usage.total_tokens", total)

    def finish_node(
        self,
        handle: TraceHandle,
        token: Token[TraceHandle | None] | None,
        *,
        output: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if token is not None:
            self._node.reset(token)
        self._finish(handle, output=output, error=error)

    def finish_graph(
        self,
        handle: TraceHandle,
        token: Token[TraceHandle | None] | None,
        *,
        output: Any = None,
        error: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        if handle.span is not None and cancelled:
            handle.span.set_attribute("data_agent.cancelled", True)
        if token is not None:
            self._root.reset(token)
        self._finish(handle, output=output, error=error)

    def _finish(self, handle: TraceHandle, *, output: Any, error: BaseException | None) -> None:
        if handle.span is None:
            return
        from opentelemetry.trace import Status, StatusCode

        try:
            self._usage_attributes(handle)
            if output is not None:
                handle.span.set_attribute("output.value", self._render(output))
            if error is None:
                handle.span.set_status(Status(StatusCode.OK))
            else:
                handle.span.set_attribute("error.type", type(error).__name__)
                handle.span.set_attribute("error.message", str(error)[:8000])
                if isinstance(error, Exception):
                    handle.span.record_exception(error)
                handle.span.set_status(Status(StatusCode.ERROR, str(error)[:1000]))
        finally:
            handle.span.end()

    async def close(self) -> None:
        if self.provider is not None:
            await asyncio.to_thread(self.provider.shutdown)
