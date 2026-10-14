"""Release identity; deployments must supply their immutable source revision."""

from __future__ import annotations

import os

VERSION = "0.2.1"


def build_identity() -> dict[str, str]:
    return {
        "version": VERSION,
        "revision": os.environ.get("DATAAGENT_BUILD_REVISION", "unpackaged-source"),
        "reportContract": "closed-claims-v2",
        "metricContract": "versioned-registry-v1",
    }
