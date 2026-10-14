#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from industrial_pdm.analyzer import IndustrialAnalyzer, as_datetime
from industrial_pdm.audit import AuditRun, json_default
from industrial_pdm.db import Database
from industrial_pdm.reporting import build_report


def main() -> None:
    default_db = f"sqlite:///{(ROOT / 'generated' / 'runtime' / 'industrial_pdm.sqlite').resolve()}"
    parser = argparse.ArgumentParser(description="Run auditable fleet analysis, anomaly detection and RCA")
    parser.add_argument("--db-url", default=os.getenv("INDUSTRIAL_PDM_DB_URL", default_db))
    parser.add_argument("--query", default="生成工业设备总体分析报告，并对指定事件给出证据化根因候选")
    parser.add_argument("--machine-id", type=int)
    parser.add_argument("--event-time")
    parser.add_argument("--component")
    parser.add_argument("--anomaly-start")
    parser.add_argument("--anomaly-end")
    parser.add_argument("--output-dir", default=str(ROOT / "generated" / "reports"))
    parser.add_argument("--log-path", default=os.getenv("INDUSTRIAL_PDM_LOG_PATH", str(ROOT / "generated" / "logs" / "analysis.jsonl")))
    args = parser.parse_args()

    if bool(args.machine_id) != bool(args.event_time):
        raise SystemExit("--machine-id and --event-time must be supplied together")

    db = Database(args.db_url)
    analyzer = IndustrialAnalyzer(db, ROOT / "config" / "rca_rules.json")
    scope = {
        "machineID": args.machine_id,
        "event_time": args.event_time,
        "component": args.component,
        "anomaly_start": args.anomaly_start,
        "anomaly_end": args.anomaly_end,
    }
    audit = AuditRun(db, args.query, scope, args.log_path).start()
    try:
        with audit.step("fleet_snapshot", "calculate_kpi", scope) as step:
            snapshot = analyzer.fleet_snapshot()
            step["output_summary"] = {
                "machine_count": snapshot["counts"]["pdm_machines"],
                "failure_count": snapshot["counts"]["pdm_failures"],
            }

        rca = None
        anomalies = []
        if args.machine_id is not None and args.event_time:
            event_dt = as_datetime(args.event_time)
            anomaly_start = as_datetime(args.anomaly_start) if args.anomaly_start else event_dt - timedelta(days=7)
            anomaly_end = as_datetime(args.anomaly_end) if args.anomaly_end else event_dt
            with audit.step(
                "detect_anomaly",
                "detect_anomaly",
                {"machineID": args.machine_id, "start": anomaly_start, "end": anomaly_end},
            ) as step:
                anomalies = analyzer.detect_anomalies(args.machine_id, anomaly_start, anomaly_end)
                analyzer.persist_anomalies(audit.run_id, anomalies)
                step["output_summary"] = {"anomaly_count": len(anomalies)}

            with audit.step(
                "root_cause_analysis",
                "root_cause_analysis",
                {"machineID": args.machine_id, "event_time": args.event_time, "component": args.component},
            ) as step:
                rca = analyzer.root_cause_analysis(
                    args.machine_id, args.event_time, component=args.component, top_k=3
                )
                analyzer.persist_rca(audit.run_id, rca)
                step["output_summary"] = {
                    "candidate_count": len(rca["candidates"]),
                    "top_candidate": rca["candidates"][0]["candidate_name"],
                    "top_score": rca["candidates"][0]["confidence_score"],
                }

        with audit.step("generate_report", "generate_report", {"output_dir": args.output_dir}) as step:
            report = build_report(
                snapshot,
                args.output_dir,
                rca=rca,
                anomalies=anomalies,
                run_id=audit.run_id,
            )
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
                    json.dumps(report["report_data"], ensure_ascii=False, default=json_default),
                    report["html_path"],
                ),
            )
            step["output_summary"] = {
                "html_path": report["html_path"],
                "json_path": report["json_path"],
            }

        audit.finish("succeeded")
    except Exception as exc:
        audit.finish("failed", str(exc))
        raise

    print(
        json.dumps(
            {
                "status": "succeeded",
                "run_id": audit.run_id,
                "html_report": report["html_path"],
                "json_report": report["json_path"],
                "anomaly_count": len(anomalies),
                "root_cause_candidates": rca["candidates"] if rca else [],
            },
            ensure_ascii=False,
            indent=2,
            default=json_default,
        )
    )


if __name__ == "__main__":
    main()
