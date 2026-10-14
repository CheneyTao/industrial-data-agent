"""Reviewed prompt templates, including adaptive Python workflow extensions."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    filename = name if name.endswith(".txt") else f"{name}.txt"
    return files("dataagent.prompts").joinpath(filename).read_text(encoding="utf-8")


def render_prompt(name: str, **values: Any) -> str:
    rendered = load_prompt(name)
    # Spring's template syntax only substitutes declared variables. Python's
    # str.format would also interpret literal JSON examples in these prompts.
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", "" if value is None else str(value))
    return rendered.replace(r"\{", "{").replace(r"\}", "}")
