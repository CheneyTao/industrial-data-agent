#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def plain(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, (set, tuple)):
        return list(value)
    return value


def add(results: list[dict[str, Any]], name: str, actual: Any, expected: Any, passed: bool, details: str = "") -> None:
    results.append(
        {
            "check": name,
            "actual": plain(actual),
            "expected": plain(expected),
            "status": "PASS" if bool(passed) else "FAIL",
            "details": details,
        }
    )


def load_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(ROOT / "synthetic" / name, encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate source, synthetic lineage, database and business invariants")
    parser.add_argument("--db", default=str(ROOT / "generated" / "runtime" / "industrial_pdm.sqlite"))
    parser.add_argument("--output", default=str(ROOT / "generated" / "reports" / "validation_results.json"))
    args = parser.parse_args()
    results: list[dict[str, Any]] = []

    failures = pd.read_csv(ROOT / "original" / "PdM_failures.csv")
    maint = pd.read_csv(ROOT / "original" / "PdM_maint.csv")
    reports = load_csv("synthetic_fault_reports.csv")
    logs = load_csv("synthetic_operator_logs.csv")
    notes = load_csv("synthetic_maintenance_notes.csv")
    work_orders = load_csv("synthetic_work_orders.csv")
    parts = load_csv("synthetic_parts_usage.csv")
    operations = load_csv("synthetic_daily_operations.csv")
    manifest = json.loads((ROOT / "synthetic" / "manifest.json").read_text(encoding="utf-8"))

    failure_keys = set(zip(failures.machineID, failures.datetime.astype(str), failures.failure))
    report_keys = set(zip(reports.machineID, reports.datetime.astype(str), reports.component))
    add(results, "failure_report_alignment", len(failure_keys & report_keys), len(failure_keys), failure_keys == report_keys)
    add(results, "fault_report_count", len(reports), len(failures), len(reports) == len(failures))

    for table_name, frame, flag_name in (
        ("synthetic_fault_reports", reports, "synthetic_text_flag"),
        ("synthetic_operator_logs", logs, "synthetic_text_flag"),
        ("synthetic_maintenance_notes", notes, "synthetic_text_flag"),
        ("synthetic_work_orders", work_orders, "synthetic_record_flag"),
        ("synthetic_parts_usage", parts, "synthetic_record_flag"),
        ("synthetic_daily_operations", operations, "synthetic_record_flag"),
    ):
        all_flagged = bool((frame[flag_name] == 1).all())
        add(results, f"synthetic_flag:{table_name}", int(frame[flag_name].sum()), len(frame), all_flagged)
        add(
            results,
            f"manifest_count:{table_name}",
            len(frame),
            int(manifest["tables"][table_name]),
            len(frame) == int(manifest["tables"][table_name]),
        )

    fault_ids = set(reports.fault_id.astype(str))
    linked_sets = {
        "operator_logs": set(logs.linked_fault_id.dropna().astype(str)) - {""},
        "maintenance_notes": set(notes.linked_fault_id.dropna().astype(str)) - {""},
        "work_orders": set(work_orders.linked_fault_id.dropna().astype(str)) - {""},
    }
    for name, linked_ids in linked_sets.items():
        invalid = sorted(linked_ids - fault_ids)
        add(results, f"linked_fault_integrity:{name}", len(invalid), 0, not invalid, ",".join(invalid[:5]))

    fault_time = reports.set_index("fault_id")["datetime"].map(pd.Timestamp)
    linked_logs = logs[logs.linked_fault_id.notna() & (logs.linked_fault_id.astype(str) != "")]
    non_precursor_count = sum(
        pd.Timestamp(row.datetime) >= fault_time.loc[str(row.linked_fault_id)]
        for row in linked_logs.itertuples(index=False)
    )
    add(results, "linked_operator_logs_strictly_before_failure", int(non_precursor_count), 0, non_precursor_count == 0)

    note_fault_coverage = set(notes.linked_fault_id.dropna().astype(str)) - {""}
    missing_note_faults = sorted(fault_ids - note_fault_coverage)
    add(results, "every_failure_has_synthetic_maintenance_note", len(missing_note_faults), 0, not missing_note_faults)

    source_maint_keys = {
        f"{int(row.machineID)}|{row.datetime}|{row.comp}" for row in maint.itertuples(index=False)
    }
    note_source_keys = set(
        notes.loc[notes.source_maintenance_exact_match.isin([0, 1]), "source_event_key"].astype(str)
    )
    missing_source_maint = sorted(key for key in source_maint_keys if key not in note_source_keys)
    add(results, "every_source_maintenance_has_synthetic_note", len(missing_source_maint), 0, not missing_source_maint)

    note_ids = set(notes.maint_note_id.astype(str))
    work_note_ids = set(work_orders.linked_maint_note_id.astype(str))
    add(results, "one_work_order_per_maintenance_note", len(work_note_ids), len(note_ids), work_note_ids == note_ids and len(work_orders) == len(notes))

    parts_totals = parts.groupby("work_order_id", as_index=False)["total_cost"].sum().rename(columns={"total_cost": "parts_sum"})
    reconciliation = work_orders[["work_order_id", "parts_cost"]].merge(parts_totals, on="work_order_id", how="left")
    reconciliation["parts_sum"] = reconciliation["parts_sum"].fillna(0.0)
    max_parts_delta = float((reconciliation.parts_cost - reconciliation.parts_sum).abs().max())
    add(results, "parts_cost_reconciliation", round(max_parts_delta, 6), 0.0, max_parts_delta < 0.001)

    operation_keys = operations[["operation_date", "machineID"]].drop_duplicates()
    add(results, "daily_operation_unique_key", len(operation_keys), len(operations), len(operation_keys) == len(operations))
    add(results, "daily_operation_machine_days", len(operations), 36500, len(operations) == 36500)
    hours_delta = float((operations.runtime_hours + operations.downtime_hours - operations.scheduled_hours).abs().max())
    units_delta = int((operations.good_units + operations.scrap_units - operations.production_units).abs().max())
    oee_expected = operations.availability_pct * operations.performance_pct * operations.quality_pct / 10000.0
    oee_delta = float((operations.oee_pct - oee_expected).abs().max())
    add(results, "daily_hours_balance", round(hours_delta, 6), 0.0, hours_delta < 0.0011)
    add(results, "daily_units_balance", units_delta, 0, units_delta == 0)
    add(results, "daily_oee_formula", round(oee_delta, 6), "<=0.0015", oee_delta <= 0.0015)

    db_path = Path(args.db)
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            expected_counts = {
                "pdm_machines": 100,
                "pdm_telemetry": 876100,
                "pdm_errors": 3919,
                "pdm_maint": 3286,
                "pdm_failures": 761,
                **{name: int(value) for name, value in manifest["tables"].items()},
            }
            for table, count in expected_counts.items():
                actual = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                add(results, f"database_row_count:{table}", actual, count, actual == count)
            required_tables = {
                "dim_machine_models",
                "data_sources",
                "analysis_runs",
                "analysis_step_logs",
                "detected_anomalies",
                "root_cause_candidates",
                "analysis_reports",
            }
            required_views = {
                "v_machine_daily_kpi",
                "v_event_timeline",
                "v_failure_rca_evidence",
                "v_machine_reliability",
                "v_monthly_operations_kpi",
                "v_work_order_kpi",
            }
            existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            existing_views = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
            missing_tables = sorted(required_tables - existing_tables)
            missing_views = sorted(required_views - existing_views)
            add(results, "analysis_tables_present", missing_tables, [], not missing_tables)
            add(results, "analytics_views_present", missing_views, [], not missing_views)
            model_catalog = json.loads(
                (ROOT / "config" / "model_catalog.json").read_text(encoding="utf-8")
            )["models"]
            expected_model_map = {
                item["source_model_code"]: item["display_name_zh"] for item in model_catalog
            }
            actual_model_map = dict(
                conn.execute(
                    "SELECT source_model_code,display_name_zh FROM dim_machine_models ORDER BY sort_order"
                ).fetchall()
            )
            add(
                results,
                "model_alias_catalog_matches_database",
                actual_model_map,
                expected_model_map,
                actual_model_map == expected_model_map,
            )
        finally:
            conn.close()
    else:
        add(results, "sqlite_database_present", str(db_path), "existing file", False)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "check_count": len(results),
        "checks": results,
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
