"""Conservative normalization guard, independent of any business schema or year."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

_NUMBERS = re.compile(r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9_.])")


def numeric_anchors(text: str) -> set[str]:
    anchors = set()
    for match in _NUMBERS.finditer(unicodedata.normalize("NFKC", text)):
        token = match.group().replace(",", "")
        suffix = "%" if token.endswith("%") else ""
        anchors.add(str(Decimal(token.rstrip("%")).normalize()) + suffix)
    return anchors


def missing_numeric_anchors(original: str, normalized: str) -> list[str]:
    """Reject lossy rewrites; the unchanged original is always a safe fallback.

    This is not a complete semantic validator. Original user input must remain
    authoritative in every downstream prompt, even when these anchors match.
    """
    return sorted(numeric_anchors(original) - numeric_anchors(normalized))
