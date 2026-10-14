from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


def clean(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _manual_sections(path: Path) -> Iterable[tuple[str, str]]:
    title = "manual"
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if lines:
                yield title, "\n".join(lines).strip()
            title = line[3:].strip()
            lines = [line]
        else:
            lines.append(line)
    if lines:
        yield title, "\n".join(lines).strip()


def build_corpus(root: str | Path, output_path: str | Path) -> dict[str, Any]:
    root = Path(root)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()

    def write(handle: Any, doc: dict[str, Any]) -> None:
        counts[doc["metadata"]["doc_type"]] += 1
        handle.write(json.dumps(doc, ensure_ascii=False) + "\n")

    with output_path.open("w", encoding="utf-8") as handle:
        reports = pd.read_csv(root / "synthetic" / "synthetic_fault_reports.csv", encoding="utf-8-sig")
        for row in reports.to_dict(orient="records"):
            write(
                handle,
                {
                    "doc_id": f"fault_report:{row['fault_id']}",
                    "content": str(row["fault_report"]),
                    "metadata": {
                        "doc_type": "fault_report",
                        "source_table": "synthetic_fault_reports",
                        "source_id": row["fault_id"],
                        "machineID": int(row["machineID"]),
                        "event_time": str(row["datetime"]),
                        "component": row["component"],
                        "linked_fault_id": row["fault_id"],
                        "evidence_level": "synthetic_aligned_to_source_failure",
                        "synthetic_text_flag": 1,
                    },
                },
            )

        logs = pd.read_csv(root / "synthetic" / "synthetic_operator_logs.csv", encoding="utf-8-sig")
        for row in logs.to_dict(orient="records"):
            linked = clean(row["linked_fault_id"])
            write(
                handle,
                {
                    "doc_id": f"operator_log:{row['log_id']}",
                    "content": str(row["operator_note"]),
                    "metadata": {
                        "doc_type": "operator_log",
                        "source_table": "synthetic_operator_logs",
                        "source_id": row["log_id"],
                        "machineID": int(row["machineID"]),
                        "event_time": str(row["datetime"]),
                        "component": row["component"],
                        "linked_fault_id": linked,
                        "evidence_level": (
                            "synthetic_aligned_to_source_failure" if linked else "synthetic_background"
                        ),
                        "synthetic_text_flag": 1,
                    },
                },
            )

        notes = pd.read_csv(root / "synthetic" / "synthetic_maintenance_notes.csv", encoding="utf-8-sig")
        for row in notes.to_dict(orient="records"):
            linked = clean(row["linked_fault_id"])
            write(
                handle,
                {
                    "doc_id": f"maintenance_note:{row['maint_note_id']}",
                    "content": str(row["technician_note"]),
                    "metadata": {
                        "doc_type": "maintenance_note",
                        "source_table": "synthetic_maintenance_notes",
                        "source_id": row["maint_note_id"],
                        "machineID": int(row["machineID"]),
                        "event_time": str(row["datetime"]),
                        "component": row["component"],
                        "linked_fault_id": linked,
                        "evidence_level": (
                            "synthetic_aligned_to_source_failure" if linked else "synthetic_background"
                        ),
                        "synthetic_text_flag": 1,
                    },
                },
            )

        for index, (title, content) in enumerate(
            _manual_sections(root / "knowledge" / "industrial_pdm_manual.md"), 1
        ):
            write(
                handle,
                {
                    "doc_id": f"manual:{index:02d}",
                    "content": content,
                    "metadata": {
                        "doc_type": "analysis_manual",
                        "source_table": "knowledge",
                        "source_id": f"manual:{index:02d}",
                        "title": title,
                        "evidence_level": "project_rule",
                        "synthetic_text_flag": 0,
                    },
                },
            )

        metric_catalog = json.loads((root / "config" / "metric_catalog.json").read_text(encoding="utf-8"))
        for metric in metric_catalog["metrics"]:
            write(
                handle,
                {
                    "doc_id": f"metric:{metric['id']}",
                    "content": json.dumps(metric, ensure_ascii=False),
                    "metadata": {
                        "doc_type": "metric_definition",
                        "source_table": "metric_catalog",
                        "source_id": metric["id"],
                        "evidence_level": "project_rule",
                        "synthetic_text_flag": 0,
                    },
                },
            )

        rca_rules = json.loads((root / "config" / "rca_rules.json").read_text(encoding="utf-8"))
        for rule in rca_rules["candidates"]:
            write(
                handle,
                {
                    "doc_id": f"rca_rule:{rule['name']}",
                    "content": json.dumps(rule, ensure_ascii=False),
                    "metadata": {
                        "doc_type": "rca_rule",
                        "source_table": "rca_rules",
                        "source_id": rule["name"],
                        "component": rule["components"][0],
                        "evidence_level": "project_rule",
                        "synthetic_text_flag": 0,
                    },
                },
            )

    manifest = {
        "corpus_path": output_path.resolve().relative_to(root.resolve()).as_posix(),
        "document_count": sum(counts.values()),
        "counts_by_doc_type": dict(sorted(counts.items())),
        "required_metadata_filters": [
            "machineID",
            "event_time",
            "component",
            "linked_fault_id",
            "doc_type",
            "synthetic_text_flag",
        ],
        "usage_boundary": "RAG supplies text/rules; numeric metrics must be calculated from the database.",
    }
    manifest_path = output_path.with_name("manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
