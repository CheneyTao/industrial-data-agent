from __future__ import annotations

import argparse
import csv
from pathlib import Path

BASE_PACKAGE = Path("com/alibaba/cloud/ai/dataagent")


def _domain_target(name: str) -> str:
    lowered = name.lower()
    if "datasource" in lowered or "relation" in lowered or "schema" in lowered:
        return "src/dataagent/connectors.py;src/dataagent/schema_index.py;src/dataagent/api/datasources.py"
    if "knowledge" in lowered:
        return "src/dataagent/models.py;src/dataagent/api/knowledge.py;src/dataagent/documents.py"
    if "semantic" in lowered:
        return "src/dataagent/models.py;src/dataagent/api/semantic.py"
    if "chat" in lowered or "session" in lowered or "message" in lowered or "preset" in lowered:
        return "src/dataagent/models.py;src/dataagent/api/chats.py;src/dataagent/session_events.py"
    if "modelconfig" in lowered or "aimodel" in lowered:
        return "src/dataagent/models.py;src/dataagent/llm.py;src/dataagent/api/configuration.py"
    if "prompt" in lowered:
        return "src/dataagent/models.py;src/dataagent/prompts/__init__.py;src/dataagent/api/configuration.py"
    if "graph" in lowered or "stream" in lowered:
        return "src/dataagent/workflow/engine.py;src/dataagent/workflow/state.py;src/dataagent/schemas.py"
    if "agent" in lowered:
        return "src/dataagent/models.py;src/dataagent/api/agents.py;src/dataagent/api/agent_datasources.py"
    if "result" in lowered or "display" in lowered or "report" in lowered:
        return "src/dataagent/schemas.py;src/dataagent/reports.py"
    return "src/dataagent/models.py;src/dataagent/schemas.py;src/dataagent/serialization.py"


def python_targets(parts: tuple[str, ...], class_name: str) -> tuple[str, str]:
    package = parts[0] if parts else "root"
    remainder = parts[1:]
    if package == "root":
        return "src/dataagent/main.py;src/dataagent/app.py", "application bootstrap"
    if package == "workflow":
        if "dispatcher" in remainder:
            return "src/dataagent/workflow/nodes.py;src/dataagent/workflow/state.py", "conditional routing"
        return "src/dataagent/workflow/nodes.py;src/dataagent/workflow/engine.py", "state graph node"
    if package == "controller":
        lowered = class_name.lower()
        if "agentdatasource" in lowered:
            target = "src/dataagent/api/agent_datasources.py"
        elif "agentknowledge" in lowered or "businessknowledge" in lowered:
            target = "src/dataagent/api/knowledge.py"
        elif "semantic" in lowered:
            target = "src/dataagent/api/semantic.py"
        elif "modelconfig" in lowered or "promptconfig" in lowered:
            target = "src/dataagent/api/configuration.py"
        elif "datasource" in lowered:
            target = "src/dataagent/api/datasources.py"
        elif "graph" in lowered:
            target = "src/dataagent/api/graph.py"
        elif "fileupload" in lowered:
            target = "src/dataagent/api/uploads.py"
        elif any(value in lowered for value in ("chat", "session", "preset")):
            target = "src/dataagent/api/chats.py"
        elif "agent" in lowered:
            target = "src/dataagent/api/agents.py"
        else:
            target = "src/dataagent/app.py;src/dataagent/errors.py"
        return target, "REST/SSE compatibility endpoint"
    if package in {"dto", "bo", "vo", "entity", "mapper", "converter", "enums"}:
        return _domain_target(class_name), f"{package} data/domain mapping"
    if package == "connector":
        return "src/dataagent/connectors.py", "database connector and metadata behavior"
    if package == "service":
        subpackage = remainder[0] if remainder else ""
        targets = {
            "agent": "src/dataagent/api/agents.py;src/dataagent/api/agent_datasources.py",
            "aimodelconfig": "src/dataagent/llm.py;src/dataagent/api/configuration.py",
            "business": "src/dataagent/api/knowledge.py;src/dataagent/maintenance.py",
            "chat": "src/dataagent/api/chats.py;src/dataagent/session_events.py;src/dataagent/reports.py",
            "code": "src/dataagent/sandbox.py;src/dataagent/workflow/nodes.py",
            "datasource": "src/dataagent/connectors.py;src/dataagent/api/datasources.py",
            "file": "src/dataagent/documents.py;src/dataagent/api/uploads.py",
            "graph": "src/dataagent/workflow/engine.py;src/dataagent/api/graph.py",
            "hybrid": "src/dataagent/vectorstore.py",
            "knowledge": (
                "src/dataagent/documents.py;src/dataagent/maintenance.py;"
                "src/dataagent/api/knowledge.py"
            ),
            "langfuse": "src/dataagent/telemetry.py",
            "llm": "src/dataagent/llm.py",
            "mcp": "src/dataagent/mcp_server.py",
            "nl2sql": "src/dataagent/workflow/engine.py;src/dataagent/workflow/nodes.py",
            "prompt": "src/dataagent/prompts/__init__.py;src/dataagent/api/configuration.py",
            "schema": "src/dataagent/schema_index.py;src/dataagent/connectors.py",
            "semantic": "src/dataagent/api/semantic.py;src/dataagent/models.py",
            "vector": "src/dataagent/vectorstore.py",
            "vectorstore": "src/dataagent/vectorstore.py",
        }
        return targets.get(subpackage, _domain_target(class_name)), f"{subpackage or 'domain'} service"
    direct = {
        "annotation": "src/dataagent/schemas.py;src/dataagent/mcp_server.py",
        "aop": "src/dataagent/prompts/__init__.py;src/dataagent/telemetry.py",
        "config": "src/dataagent/config.py;src/dataagent/app.py;src/dataagent/workflow/state.py",
        "constant": "src/dataagent/workflow/state.py;src/dataagent/vectorstore.py",
        "event": "src/dataagent/maintenance.py;src/dataagent/session_events.py",
        "exception": "src/dataagent/errors.py;src/dataagent/app.py",
        "prompt": "src/dataagent/prompts/__init__.py;src/dataagent/prompts",
        "properties": "src/dataagent/config.py",
        "security": "src/dataagent/security.py;src/dataagent/api/graph.py",
        "splitter": "src/dataagent/documents.py",
        "strategy": "src/dataagent/workflow/engine.py;src/dataagent/models.py",
        "util": _domain_target(class_name),
    }
    return direct.get(package, "src/dataagent/app.py"), f"{package} infrastructure"


def generate(source: Path, output: Path) -> int:
    java_root = source / "data-agent-management" / "src" / "main" / "java"
    package_root = java_root / BASE_PACKAGE
    rows: list[dict[str, str]] = []
    for path in sorted(package_root.rglob("*.java")):
        relative_package = path.relative_to(package_root)
        parts = relative_package.parts[:-1]
        class_name = path.stem
        category_parts = tuple(parts) if parts else ("root",)
        targets, note = python_targets(category_parts, class_name)
        rows.append(
            {
                "java_path": path.relative_to(source).as_posix(),
                "java_class": class_name,
                "java_package": ".".join(category_parts),
                "python_targets": targets,
                "coverage": "implemented",
                "mapping_note": note,
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count = generate(args.source.resolve(), args.output.resolve())
    print(f"Mapped {count} Java production files")


if __name__ == "__main__":
    main()
