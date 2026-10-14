from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from .analyzer import IndustrialAnalyzer, as_datetime
from .audit import AuditRun
from .db import Database
from .provenance import find_run_manifest
from .reporting import build_report


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = f"sqlite:///{(ROOT / 'generated' / 'runtime' / 'industrial_pdm.sqlite').resolve()}"


def _services() -> tuple[Database, IndustrialAnalyzer]:
    db = Database(os.getenv("INDUSTRIAL_PDM_DB_URL", DEFAULT_DB))
    analyzer = IndustrialAnalyzer(db, ROOT / "config" / "rca_rules.json")
    return db, analyzer


def _load_run_trace(run_id: str) -> dict[str, Any]:
    manifest_path = find_run_manifest(ROOT / "generated" / "reports", run_id)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - dependency message for deployment
    raise RuntimeError("Install compatible MCP support with: pip install 'mcp>=1.0,<2'") from exc


mcp = FastMCP("industrial-pdm-analytics")


@mcp.tool()
def get_schema() -> dict[str, Any]:
    """Return data-source tables, relationships and evidence boundaries."""
    schema = json.loads((ROOT / "config" / "datasource.json").read_text(encoding="utf-8"))
    schema["model_catalog"] = json.loads(
        (ROOT / "config" / "model_catalog.json").read_text(encoding="utf-8")
    )
    return schema


@mcp.tool()
def get_run_trace(run_id: str) -> dict[str, Any]:
    """Return the complete evidence and artifact lineage for one analysis run."""
    return _load_run_trace(run_id)


@mcp.tool()
def get_artifact(run_id: str, artifact_id: str) -> dict[str, Any]:
    """Return one trace artifact, including its hash, parents, metadata and inline evidence."""
    trace = _load_run_trace(run_id)
    for artifact in trace["artifacts"]:
        if artifact["artifact_id"] == artifact_id:
            return artifact
    raise ValueError(f"artifact_id={artifact_id!r} does not belong to run_id={run_id}")


@mcp.tool()
def query_database(sql: str) -> list[dict[str, Any]]:
    """Execute one read-only SQL statement against the configured industrial data source."""
    db, _ = _services()
    rows = db.read_only_query(sql)
    return rows[:5000]


@mcp.tool()
def calculate_kpi() -> dict[str, Any]:
    """Calculate fleet counts, trends, distributions and risk rankings from database facts."""
    _, analyzer = _services()
    return analyzer.fleet_snapshot()


@mcp.tool()
def detect_anomaly(
    machine_id: int,
    start_time: str,
    end_time: str,
    z_threshold: float = 3.5,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Detect sensor deviations using a prior-only rolling median/MAD baseline."""
    _, analyzer = _services()
    return analyzer.detect_anomalies(machine_id, start_time, end_time, z_threshold, limit=limit)


@mcp.tool()
def get_related_events(
    machine_id: int,
    event_time: str,
    lookback_hours: int = 72,
    lookahead_hours: int = 0,
) -> list[dict[str, Any]]:
    """Return factual and synthetic timeline events with explicit evidence labels."""
    _, analyzer = _services()
    return analyzer.related_events(machine_id, event_time, lookback_hours, lookahead_hours)


@mcp.tool()
def root_cause_analysis(
    machine_id: int,
    event_time: str,
    component: str,
    top_k: int = 3,
) -> dict[str, Any]:
    """Rank evidence-backed root-cause candidates; scores are not causal probabilities."""
    _, analyzer = _services()
    return analyzer.root_cause_analysis(machine_id, event_time, component, top_k)


@mcp.tool()
def generate_report(
    machine_id: int | None = None,
    event_time: str | None = None,
    component: str | None = None,
) -> dict[str, Any]:
    """Generate an auditable HTML/JSON fleet report with optional event RCA."""
    if bool(machine_id) != bool(event_time):
        raise ValueError("machine_id and event_time must be provided together")
    db, analyzer = _services()
    scope = {"machineID": machine_id, "event_time": event_time, "component": component}
    audit = AuditRun(
        db,
        "MCP generate_report",
        scope,
        os.getenv("INDUSTRIAL_PDM_LOG_PATH", str(ROOT / "generated" / "logs" / "analysis.jsonl")),
    ).start()
    try:
        with audit.step("fleet_snapshot", "calculate_kpi", scope):
            snapshot = analyzer.fleet_snapshot()
        rca = None
        anomalies = []
        if machine_id is not None and event_time:
            event_dt = as_datetime(event_time)
            with audit.step("detect_anomaly", "detect_anomaly", scope):
                anomalies = analyzer.detect_anomalies(machine_id, event_dt - timedelta(days=7), event_dt)
                analyzer.persist_anomalies(audit.run_id, anomalies)
            with audit.step("root_cause_analysis", "root_cause_analysis", scope):
                rca = analyzer.root_cause_analysis(machine_id, event_time, component, 3)
                analyzer.persist_rca(audit.run_id, rca)
        with audit.step("generate_report", "generate_report", scope):
            report = build_report(snapshot, ROOT / "generated" / "reports", rca, anomalies, audit.run_id)
            db.execute(
                "INSERT INTO analysis_reports "
                "(report_id, run_id, report_type, title, executive_summary, report_json, html_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    report["report_id"],
                    audit.run_id,
                    "fleet_with_event_rca" if rca else "fleet",
                    report["title"],
                    report["executive_summary"],
                    json.dumps(report["report_data"], ensure_ascii=False),
                    report["html_path"],
                ),
            )
        audit.finish("succeeded")
        return {
            "run_id": audit.run_id,
            "html_path": report["html_path"],
            "json_path": report["json_path"],
            "summary": report["executive_summary"],
        }
    except Exception as exc:
        audit.finish("failed", str(exc))
        raise


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
