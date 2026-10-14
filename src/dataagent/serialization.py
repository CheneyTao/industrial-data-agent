from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from dataagent.models import Base, model_columns


def camel(value: str) -> str:
    return re.sub(r"_([a-zA-Z0-9])", lambda match: match.group(1).upper(), value)


def serialize(value: Any, *, exclude: set[str] | None = None) -> Any:
    if isinstance(value, Base):
        return {camel(key): serialize(item) for key, item in model_columns(value, exclude=exclude).items()}
    if isinstance(value, dict):
        return {camel(str(key)): serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
