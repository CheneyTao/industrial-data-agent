"""Request-scoped, local model-call evidence independent of external telemetry."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_sink: ContextVar[Callable[[dict[str, Any]], object] | None] = ContextVar("model_call_sink", default=None)


@contextmanager
def capture_model_calls(sink: Callable[[dict[str, Any]], object]) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def record_model_call(event: dict[str, Any]) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(event)
