#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_pdm.rag import build_corpus


if __name__ == "__main__":
    result = build_corpus(ROOT, ROOT / "generated" / "rag" / "industrial_pdm_documents.jsonl")
    print(json.dumps(result, ensure_ascii=False, indent=2))
