from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".nuxt",
    ".output",
    ".pytest_cache",
    ".release-build",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "htmlcov",
    "node_modules",
    "uploads",
    "work",
}
EXCLUDED_FILES = {".coverage", ".env", "coverage.xml"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}


def _included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts[:-1]):
        return False
    if path.name in EXCLUDED_FILES or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.name.endswith(".tar.gz"):
        return False
    return True


def build_archive(root: Path, output: Path) -> dict[str, int | str]:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for current, directories, names in os.walk(root):
        directories[:] = sorted(
            directory for directory in directories if directory not in EXCLUDED_DIRECTORIES
        )
        files.extend(
            path
            for name in names
            if _included(path := Path(current, name), root)
        )
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    if not files:
        raise RuntimeError(f"No release files found under {root}")

    prefix = "DataAgent-Python"
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            archive.write(path, f"{prefix}/{relative}")

    return {
        "archive": str(output),
        "fileCount": len(files),
        "bytes": output.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the clean full-repository release archive")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_archive(args.root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
