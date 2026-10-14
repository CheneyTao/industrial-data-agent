from __future__ import annotations

import math
import re
from dataclasses import dataclass

TRUNCATION_MARKER = "\n\n...[context compacted to fit the model token budget]...\n\n"
_TOKEN_PARTS = re.compile(r"[A-Za-z0-9_]+|[ \t]+|\r\n|\n|[^\x00-\x7f]|.", re.DOTALL)


class ContextBudgetError(ValueError):
    """Raised when fixed prompt content leaves no safe room for user input."""


def token_count(text: str) -> int:
    """Return an offline, deliberately conservative tokenizer estimate.

    A database-configured OpenAI-compatible endpoint can front Qwen, GPT, or
    another tokenizer, so pretending one local vocabulary is exact is unsafe.
    This estimator slightly over-counts ordinary English and CJK and never
    downloads tokenizer assets at runtime. Provider overflow errors remain the
    final feedback signal and trigger a stricter retry.
    """

    total = 0
    for part in _TOKEN_PARTS.findall(text):
        if part.isascii() and (part[0].isalnum() or part[0] == "_"):
            total += math.ceil(len(part) / 3)
        elif part.isascii() and part.isspace():
            total += max(1, math.ceil(len(part) / 4))
        elif part.isascii():
            total += len(part)
        else:
            total += max(1, math.ceil(len(part.encode("utf-8")) / 2))
    return total


def _take_prefix(text: str, max_tokens: int) -> str:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if token_count(text[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _take_suffix(text: str, max_tokens: int) -> str:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if token_count(text[len(text) - middle :]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[len(text) - low :] if low else ""


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Keep both instructions at the front and schemas/current data at the end."""

    if max_tokens <= 0:
        return ""
    if token_count(text) <= max_tokens:
        return text

    marker_tokens = token_count(TRUNCATION_MARKER)
    if max_tokens <= marker_tokens + 2:
        return _take_prefix(text, max_tokens)

    content_budget = max_tokens - marker_tokens
    head_budget = max(1, int(content_budget * 0.6))
    tail_budget = content_budget - head_budget
    return _take_prefix(text, head_budget) + TRUNCATION_MARKER + _take_suffix(text, tail_budget)


@dataclass(frozen=True, slots=True)
class BudgetedPrompt:
    system: str
    user: str
    estimated_input_tokens: int
    user_was_truncated: bool


def fit_chat_prompt(
    *,
    system: str,
    user: str,
    context_window_tokens: int,
    max_output_tokens: int,
    safety_tokens: int,
    input_ratio: float = 1.0,
) -> BudgetedPrompt:
    """Fit a two-message chat request inside a conservative token budget.

    The system message is never silently truncated because it contains security
    and behavioural constraints. Only task data in the user message is compacted.
    """

    if not 0 < input_ratio <= 1:
        raise ContextBudgetError("input_ratio must be in the range (0, 1]")
    if context_window_tokens <= max_output_tokens + safety_tokens:
        raise ContextBudgetError(
            "Chat context window must exceed max output tokens plus the safety reserve"
        )

    # Account for role/message framing used by OpenAI-compatible chat endpoints.
    framing_tokens = 16
    full_input_budget = context_window_tokens - max_output_tokens - safety_tokens
    input_budget = max(1, int(full_input_budget * input_ratio))
    system_tokens = token_count(system) + framing_tokens
    user_budget = input_budget - system_tokens
    if user_budget < 32:
        raise ContextBudgetError(
            "System prompt leaves fewer than 32 tokens for user input; increase the context window "
            "or reduce the configured max output"
        )

    fitted_user = truncate_to_tokens(user, user_budget)
    return BudgetedPrompt(
        system=system,
        user=fitted_user,
        estimated_input_tokens=system_tokens + token_count(fitted_user),
        user_was_truncated=fitted_user != user,
    )


def is_context_overflow_error(status_code: int, body: str) -> bool:
    if status_code not in {400, 413, 422}:
        return False
    normalized = body.lower()
    signals = (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "prompt is too long",
        "input is too long",
        "input length exceeds",
        "max context",
        "token limit",
    )
    return any(signal in normalized for signal in signals)
