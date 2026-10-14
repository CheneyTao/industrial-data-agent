#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


TABLES = [
    ("pdm_machines", ROOT / "original" / "PdM_machines.csv", "utf-8", None),
    ("pdm_telemetry", ROOT / "original" / "PdM_telemetry.csv", "utf-8", 100_000),
    ("pdm_errors", ROOT / "original" / "PdM_errors.csv", "utf-8", None),
    ("pdm_maint", ROOT / "original" / "PdM_maint.csv", "utf-8", None),
    ("pdm_failures", ROOT / "original" / "PdM_failures.csv", "utf-8", None),
    ("synthetic_fault_reports", ROOT / "synthetic" / "synthetic_fault_reports.csv", "utf-8-sig", None),
    ("synthetic_operator_logs", ROOT / "synthetic" / "synthetic_operator_logs.csv", "utf-8-sig", None),
    ("synthetic_maintenance_notes", ROOT / "synthetic" / "synthetic_maintenance_notes.csv", "utf-8-sig", None),
    ("synthetic_work_orders", ROOT / "synthetic" / "synthetic_work_orders.csv", "utf-8-sig", None),
    ("synthetic_parts_usage", ROOT / "synthetic" / "synthetic_parts_usage.csv", "utf-8-sig", None),
    ("synthetic_daily_operations", ROOT / "synthetic" / "synthetic_daily_operations.csv", "utf-8-sig", None),
]


ANALYSIS_SCHEMA = """
CREATE TABLE dim_machine_models (
  source_model_code TEXT PRIMARY KEY,
  display_name_zh TEXT NOT NULL UNIQUE,
  display_name_en TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  is_demo_alias INTEGER NOT NULL CHECK (is_demo_alias = 1),
  note TEXT NOT NULL
);
CREATE TABLE data_sources (
  source_id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  database_name TEXT NOT NULL,
  description TEXT NOT NULL,
  fact_boundary TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE analysis_runs (
  run_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  requested_query TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER,
  error_message TEXT,
  FOREIGN KEY (source_id) REFERENCES data_sources(source_id)
);
CREATE TABLE analysis_step_logs (
  step_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step_name TEXT NOT NULL,
  tool_name TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER,
  input_summary_json TEXT,
  output_summary_json TEXT,
  error_message TEXT,
  FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE detected_anomalies (
  anomaly_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  machineID INTEGER NOT NULL,
  event_time TEXT NOT NULL,
  metric TEXT NOT NULL,
  actual_value REAL NOT NULL,
  baseline_value REAL,
  robust_z REAL NOT NULL,
  direction TEXT NOT NULL,
  severity TEXT NOT NULL,
  method TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE root_cause_candidates (
  candidate_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  fault_id TEXT,
  anomaly_id TEXT,
  candidate_rank INTEGER NOT NULL,
  candidate_name TEXT NOT NULL,
  confidence_score REAL NOT NULL,
  evidence_json TEXT NOT NULL,
  limitations_text TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE analysis_reports (
  report_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  report_type TEXT NOT NULL,
  title TEXT NOT NULL,
  executive_summary TEXT NOT NULL,
  report_json TEXT NOT NULL,
  html_path TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX idx_analysis_runs_source_started ON analysis_runs(source_id, started_at);
CREATE INDEX idx_step_logs_run_step ON analysis_step_logs(run_id, step_name);
CREATE INDEX idx_anomaly_machine_time ON detected_anomalies(machineID, event_time);
CREATE INDEX idx_rca_run_score ON root_cause_candidates(run_id, confidence_score);
"""


VIEWS = """
CREATE VIEW v_machine_daily_kpi AS
SELECT substr(t.datetime, 1, 10) AS metric_date, t.machineID,
       m.model AS model_code, d.display_name_zh AS model_name, m.model, m.age,
       COUNT(*) AS telemetry_points,
       AVG(t.volt) AS avg_volt, AVG(t.rotate) AS avg_rotate,
       AVG(t.pressure) AS avg_pressure, AVG(t.vibration) AS avg_vibration,
       (SELECT COUNT(*) FROM pdm_errors e
        WHERE e.machineID=t.machineID AND substr(e.datetime,1,10)=substr(t.datetime,1,10)) AS error_count,
       (SELECT COUNT(*) FROM pdm_failures f
        WHERE f.machineID=t.machineID AND substr(f.datetime,1,10)=substr(t.datetime,1,10)) AS failure_count
FROM pdm_telemetry t
JOIN pdm_machines m ON m.machineID=t.machineID
JOIN dim_machine_models d ON d.source_model_code=m.model
GROUP BY substr(t.datetime,1,10), t.machineID, m.model, d.display_name_zh, m.age;
CREATE VIEW v_event_timeline AS
SELECT machineID, datetime AS event_time, 'error' AS event_type, errorID AS event_code,
       NULL AS fault_id, 'source' AS data_origin, 0 AS synthetic_text_flag FROM pdm_errors
UNION ALL
SELECT machineID, datetime, 'maintenance', comp, NULL, 'source', 0 FROM pdm_maint
UNION ALL
SELECT f.machineID, f.datetime, 'failure', f.failure, r.fault_id, 'source', 0
FROM pdm_failures f LEFT JOIN synthetic_fault_reports r
  ON r.machineID=f.machineID AND r.datetime=f.datetime AND r.component=f.failure
UNION ALL
SELECT machineID, datetime, 'synthetic_operator_log', log_type, linked_fault_id,
       'synthetic_derived', synthetic_text_flag
FROM synthetic_operator_logs;
CREATE VIEW v_failure_rca_evidence AS
SELECT r.*,
       (SELECT COUNT(*) FROM synthetic_operator_logs o WHERE o.linked_fault_id=r.fault_id) AS linked_operator_log_count,
       (SELECT COUNT(*) FROM synthetic_maintenance_notes n WHERE n.linked_fault_id=r.fault_id) AS linked_maintenance_note_count
FROM synthetic_fault_reports r;
CREATE VIEW v_machine_reliability AS
SELECT m.machineID, m.model AS model_code, d.display_name_zh AS model_name, m.model, m.age,
       COALESCE(t.observed_hours, 0) AS observed_hours,
       COALESCE(e.error_count, 0) AS error_count,
       COALESCE(mt.maintenance_count, 0) AS maintenance_count,
       COALESCE(f.failure_count, 0) AS failure_count,
       CASE WHEN COALESCE(t.observed_hours, 0) = 0 THEN 0.0
            ELSE COALESCE(f.failure_count, 0) * 1000.0 / t.observed_hours END AS failures_per_1000_observed_hours
FROM pdm_machines m
JOIN dim_machine_models d ON d.source_model_code=m.model
LEFT JOIN (SELECT machineID, COUNT(*) AS observed_hours FROM pdm_telemetry GROUP BY machineID) t ON t.machineID=m.machineID
LEFT JOIN (SELECT machineID, COUNT(*) AS error_count FROM pdm_errors GROUP BY machineID) e ON e.machineID=m.machineID
LEFT JOIN (SELECT machineID, COUNT(*) AS maintenance_count FROM pdm_maint GROUP BY machineID) mt ON mt.machineID=m.machineID
LEFT JOIN (SELECT machineID, COUNT(*) AS failure_count FROM pdm_failures GROUP BY machineID) f ON f.machineID=m.machineID;
CREATE VIEW v_monthly_operations_kpi AS
SELECT substr(o.operation_date, 1, 7) AS operation_month,
       o.model AS model_code, d.display_name_zh AS model_name, o.model,
       SUM(o.production_units) AS production_units,
       SUM(o.good_units) AS good_units,
       SUM(o.scrap_units) AS scrap_units,
       ROUND(AVG(o.oee_pct), 3) AS avg_oee_pct,
       ROUND(SUM(o.downtime_hours), 3) AS downtime_hours,
       ROUND(SUM(o.energy_kwh), 3) AS energy_kwh
FROM synthetic_daily_operations o
JOIN dim_machine_models d ON d.source_model_code=o.model
GROUP BY substr(o.operation_date, 1, 7), o.model, d.display_name_zh;
CREATE VIEW v_work_order_kpi AS
SELECT component, work_order_type,
       COUNT(*) AS work_order_count,
       ROUND(SUM(downtime_hours), 3) AS downtime_hours,
       ROUND(SUM(labor_cost), 2) AS labor_cost,
       ROUND(SUM(parts_cost), 2) AS parts_cost,
       ROUND(SUM(total_cost), 2) AS total_cost
FROM synthetic_work_orders
GROUP BY component, work_order_type;
"""


def load_table(conn: sqlite3.Connection, table: str, path: Path, encoding: str, chunk_size: int | None) -> int:
    started = time.perf_counter()
    if chunk_size:
        total = 0
        for index, frame in enumerate(pd.read_csv(path, encoding=encoding, chunksize=chunk_size)):
            frame.to_sql(table, conn, if_exists="replace" if index == 0 else "append", index=False)
            total += len(frame)
            print(f"  {table}: {total:,} rows", flush=True)
    else:
        frame = pd.read_csv(path, encoding=encoding)
        frame.to_sql(table, conn, if_exists="replace", index=False)
        total = len(frame)
    elapsed = time.perf_counter() - started
    print(f"loaded {table}: {total:,} rows in {elapsed:.1f}s")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a verified local SQLite mirror of the MySQL data source")
    parser.add_argument("--output", default=str(ROOT / "generated" / "runtime" / "industrial_pdm.sqlite"))
    parser.add_argument("--force", action="store_true", help="Replace the selected runtime database")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if not args.force:
            raise SystemExit(f"Database already exists: {output}. Use --force to replace it.")
        output.unlink()

    conn = sqlite3.connect(output)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cache_size = -200000")
        for table, path, encoding, chunk_size in TABLES:
            load_table(conn, table, path, encoding, chunk_size)

        conn.executescript(
            """
            CREATE UNIQUE INDEX uq_machine ON pdm_machines(machineID);
            CREATE UNIQUE INDEX uq_telemetry ON pdm_telemetry(machineID, datetime);
            CREATE INDEX idx_telemetry_datetime ON pdm_telemetry(datetime);
            CREATE INDEX idx_errors_machine_datetime ON pdm_errors(machineID, datetime);
            CREATE INDEX idx_maint_machine_datetime ON pdm_maint(machineID, datetime);
            CREATE INDEX idx_failures_machine_datetime ON pdm_failures(machineID, datetime);
            CREATE UNIQUE INDEX uq_synthetic_fault_event ON synthetic_fault_reports(machineID, datetime, component);
            CREATE INDEX idx_synthetic_fault_machine_datetime ON synthetic_fault_reports(machineID, datetime);
            CREATE INDEX idx_synthetic_operator_machine_datetime ON synthetic_operator_logs(machineID, datetime);
            CREATE INDEX idx_synthetic_operator_linked_fault ON synthetic_operator_logs(linked_fault_id);
            CREATE INDEX idx_synthetic_notes_machine_datetime ON synthetic_maintenance_notes(machineID, datetime);
            CREATE INDEX idx_synthetic_notes_linked_fault ON synthetic_maintenance_notes(linked_fault_id);
            CREATE UNIQUE INDEX uq_synthetic_work_order ON synthetic_work_orders(work_order_id);
            CREATE INDEX idx_synthetic_work_order_machine_opened ON synthetic_work_orders(machineID, opened_at);
            CREATE INDEX idx_synthetic_work_order_fault ON synthetic_work_orders(linked_fault_id);
            CREATE UNIQUE INDEX uq_synthetic_parts_usage ON synthetic_parts_usage(usage_id);
            CREATE INDEX idx_synthetic_parts_work_order ON synthetic_parts_usage(work_order_id);
            CREATE UNIQUE INDEX uq_synthetic_daily_operation ON synthetic_daily_operations(machineID, operation_date);
            CREATE INDEX idx_synthetic_daily_model_date ON synthetic_daily_operations(model, operation_date);
            """
        )
        conn.executescript(ANALYSIS_SCHEMA)
        model_catalog = json.loads((ROOT / "config" / "model_catalog.json").read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT INTO dim_machine_models "
            "(source_model_code, display_name_zh, display_name_en, sort_order, is_demo_alias, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    item["source_model_code"],
                    item["display_name_zh"],
                    item["display_name_en"],
                    item["sort_order"],
                    item["is_demo_alias"],
                    item["note"],
                )
                for item in model_catalog["models"]
            ],
        )
        conn.execute(
            "INSERT INTO data_sources "
            "(source_id, source_name, source_type, database_name, description, fact_boundary, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                "industrial_pdm",
                "Microsoft Azure Predictive Maintenance 扩展数据源",
                "sqlite",
                str(output),
                "MySQL 数据源的本地验证镜像，可直接运行分析、归因和报告。",
                "pdm_* 为来源结构化事实；synthetic_* 均为可复现演示衍生数据，不能当作现场事实。",
            ),
        )
        conn.executescript(VIEWS)
        conn.commit()
    finally:
        conn.close()
    print(f"SQLite data source ready: {output}")


if __name__ == "__main__":
    main()
