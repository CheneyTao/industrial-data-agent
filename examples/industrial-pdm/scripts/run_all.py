#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    command = [sys.executable, *args]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build, validate and demonstrate the industrial data source")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--skip-bootstrap", action="store_true")
    args = parser.parse_args()
    if not args.skip_generate:
        run("scripts/generate_synthetic_data.py")
    if not args.skip_bootstrap:
        run("scripts/bootstrap_sqlite.py", "--force")
    run("scripts/build_rag_corpus.py")
    run("scripts/validate_all.py")
    run("scripts/verify_nl2sql_cases.py")
    run(
        "scripts/run_analysis.py",
        "--machine-id",
        "90",
        "--event-time",
        "2015-06-04 06:00:00",
        "--component",
        "comp3",
    )


if __name__ == "__main__":
    main()
