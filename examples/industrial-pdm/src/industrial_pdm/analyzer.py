from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .db import Database


METRICS = ("volt", "rotate", "pressure", "vibration")
RCA_LIMITATION = (
    "该结果是基于遥测、错误、维护、机龄和部件规则的候选排序，不是现场拆检确认，"
    "confidence_score 也不是因果概率。"
)


def to_builtin(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat(sep=" ")
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(v) for v in value]
    return value


def as_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return pd.Timestamp(value).to_pydatetime().replace(tzinfo=None)


class IndustrialAnalyzer:
    def __init__(self, db: Database, rules_path: str | Path):
        self.db = db
        with Path(rules_path).open("r", encoding="utf-8") as handle:
            self.rules = json.load(handle)

    def fleet_snapshot(self) -> dict[str, Any]:
        counts = {}
        for table in (
            "pdm_machines",
            "pdm_telemetry",
            "pdm_errors",
            "pdm_maint",
            "pdm_failures",
            "synthetic_fault_reports",
            "synthetic_operator_logs",
            "synthetic_maintenance_notes",
            "synthetic_work_orders",
            "synthetic_parts_usage",
            "synthetic_daily_operations",
        ):
            counts[table] = int(self.db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")["n"])

        telemetry_range = self.db.fetch_one(
            "SELECT MIN(datetime) AS start_time, MAX(datetime) AS end_time FROM pdm_telemetry"
        )
        failures = self.db.query_df(
            "SELECT f.datetime, f.machineID, f.failure AS component, "
            "m.model AS model_code, d.display_name_zh AS model_name, m.age "
            "FROM pdm_failures f JOIN pdm_machines m ON m.machineID = f.machineID "
            "JOIN dim_machine_models d ON d.source_model_code = m.model"
        )
        failures["datetime"] = pd.to_datetime(failures["datetime"])
        failures["month"] = failures["datetime"].dt.strftime("%Y-%m")

        monthly = (
            failures.groupby("month", as_index=False)
            .size()
            .rename(columns={"size": "failure_count"})
            .sort_values("month")
        )
        components = (
            failures.groupby("component", as_index=False)
            .size()
            .rename(columns={"size": "failure_count"})
            .sort_values("failure_count", ascending=False)
        )
        models = (
            failures.groupby(["model_code", "model_name"], as_index=False)
            .size()
            .rename(columns={"size": "failure_count"})
            .sort_values("failure_count", ascending=False)
        )
        high_risk = self.db.query_df(
            "SELECT m.machineID, m.model AS model_code, d.display_name_zh AS model_name, m.age, "
            "COALESCE(f.failure_count, 0) AS failure_count, "
            "COALESCE(e.error_count, 0) AS error_count "
            "FROM pdm_machines m "
            "JOIN dim_machine_models d ON d.source_model_code = m.model "
            "LEFT JOIN (SELECT machineID, COUNT(*) AS failure_count FROM pdm_failures GROUP BY machineID) f "
            "ON f.machineID = m.machineID "
            "LEFT JOIN (SELECT machineID, COUNT(*) AS error_count FROM pdm_errors GROUP BY machineID) e "
            "ON e.machineID = m.machineID "
            "ORDER BY failure_count DESC, error_count DESC, m.machineID LIMIT 10"
        )
        root_causes = self.db.query_df(
            "SELECT root_cause, COUNT(*) AS event_count FROM synthetic_fault_reports "
            "GROUP BY root_cause ORDER BY event_count DESC, root_cause"
        )
        work_order_summary = self.db.fetch_one(
            "SELECT COUNT(*) AS work_order_count, ROUND(SUM(total_cost), 2) AS total_cost, "
            "ROUND(SUM(downtime_hours), 2) AS downtime_hours "
            "FROM synthetic_work_orders"
        )
        operations_summary = self.db.fetch_one(
            "SELECT SUM(production_units) AS production_units, SUM(good_units) AS good_units, "
            "SUM(scrap_units) AS scrap_units, ROUND(AVG(oee_pct), 3) AS avg_oee_pct, "
            "ROUND(SUM(energy_kwh), 3) AS energy_kwh "
            "FROM synthetic_daily_operations"
        )
        work_orders_by_component = self.db.query_df(
            "SELECT component, COUNT(*) AS work_order_count, "
            "ROUND(SUM(total_cost), 2) AS total_cost, "
            "ROUND(SUM(downtime_hours), 2) AS downtime_hours "
            "FROM synthetic_work_orders GROUP BY component ORDER BY total_cost DESC"
        )
        oee_by_model = self.db.query_df(
            "SELECT o.model AS model_code, d.display_name_zh AS model_name, "
            "ROUND(AVG(o.oee_pct), 3) AS avg_oee_pct, "
            "SUM(o.production_units) AS production_units, SUM(o.scrap_units) AS scrap_units "
            "FROM synthetic_daily_operations o "
            "JOIN dim_machine_models d ON d.source_model_code = o.model "
            "GROUP BY o.model, d.display_name_zh ORDER BY avg_oee_pct DESC"
        )
        month_expr = (
            "DATE_FORMAT(opened_at, '%Y-%m')" if self.db.dialect == "mysql" else "substr(opened_at, 1, 7)"
        )
        monthly_costs = self.db.query_df(
            f"SELECT {month_expr} AS month, ROUND(SUM(total_cost), 2) AS total_cost, "
            "ROUND(SUM(downtime_hours), 2) AS downtime_hours, COUNT(*) AS work_order_count "
            "FROM synthetic_work_orders GROUP BY " + month_expr + " ORDER BY month"
        )
        telemetry_points = counts["pdm_telemetry"]
        failure_rate = counts["pdm_failures"] / telemetry_points * 1000 if telemetry_points else 0.0
        result = {
            "source_id": "industrial_pdm",
            "counts": counts,
            "telemetry_range": telemetry_range,
            "failure_rate_per_1000_observed_hours": round(failure_rate, 4),
            "monthly_failures": monthly.to_dict(orient="records"),
            "failure_by_component": components.to_dict(orient="records"),
            "failure_by_model": models.to_dict(orient="records"),
            "top_machines": high_risk.to_dict(orient="records"),
            "synthetic_root_cause_labels": root_causes.to_dict(orient="records"),
            "synthetic_work_order_summary": work_order_summary,
            "synthetic_operations_summary": operations_summary,
            "synthetic_work_orders_by_component": work_orders_by_component.to_dict(orient="records"),
            "synthetic_oee_by_model": oee_by_model.to_dict(orient="records"),
            "synthetic_monthly_costs": monthly_costs.to_dict(orient="records"),
            "fact_boundary": {
                "source_fact_tables": [
                    "pdm_machines",
                    "pdm_telemetry",
                    "pdm_errors",
                    "pdm_maint",
                    "pdm_failures",
                ],
                "synthetic_derived_tables": [
                    "synthetic_fault_reports",
                    "synthetic_operator_logs",
                    "synthetic_maintenance_notes",
                    "synthetic_work_orders",
                    "synthetic_parts_usage",
                    "synthetic_daily_operations",
                ],
            },
        }
        return to_builtin(result)

    def detect_anomalies(
        self,
        machine_id: int,
        start_time: str | datetime,
        end_time: str | datetime,
        z_threshold: float = 3.5,
        rolling_window: int = 24,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        start_dt, end_dt = as_datetime(start_time), as_datetime(end_time)
        df = self.db.query_df(
            "SELECT datetime, machineID, volt, rotate, pressure, vibration "
            "FROM pdm_telemetry WHERE machineID = ? AND datetime BETWEEN ? AND ? ORDER BY datetime",
            (machine_id, start_dt, end_dt),
        )
        if df.empty:
            return []
        df["datetime"] = pd.to_datetime(df["datetime"])
        found: list[dict[str, Any]] = []
        min_periods = max(8, rolling_window // 2)
        for metric in METRICS:
            series = pd.to_numeric(df[metric], errors="coerce")
            past = series.shift(1)
            median = past.rolling(rolling_window, min_periods=min_periods).median()
            mad = past.rolling(rolling_window, min_periods=min_periods).apply(
                lambda values: np.median(np.abs(values - np.median(values))), raw=True
            )
            robust_z = 0.6745 * (series - median) / mad.replace(0, np.nan)
            mean = past.rolling(rolling_window, min_periods=min_periods).mean()
            std = past.rolling(rolling_window, min_periods=min_periods).std(ddof=0)
            fallback_z = (series - mean) / std.replace(0, np.nan)
            robust_z = robust_z.fillna(fallback_z)
            mask = robust_z.abs() >= z_threshold
            for index in df.index[mask]:
                score = float(robust_z.loc[index])
                severity = "high" if abs(score) >= 5 else "medium" if abs(score) >= 4 else "low"
                found.append(
                    {
                        "anomaly_id": str(uuid.uuid4()),
                        "machineID": int(machine_id),
                        "event_time": df.loc[index, "datetime"],
                        "metric": metric,
                        "actual_value": float(series.loc[index]),
                        "baseline_value": float(median.loc[index]) if pd.notna(median.loc[index]) else None,
                        "robust_z": round(score, 6),
                        "direction": "high" if score > 0 else "low",
                        "severity": severity,
                        "method": f"rolling_median_mad_{rolling_window}",
                        "evidence": {
                            "window_points": rolling_window,
                            "threshold": z_threshold,
                            "uses_only_prior_points": True,
                        },
                    }
                )
        found.sort(key=lambda item: abs(item["robust_z"]), reverse=True)
        return to_builtin(found[:limit])

    def related_events(
        self,
        machine_id: int,
        event_time: str | datetime,
        lookback_hours: int = 72,
        lookahead_hours: int = 0,
    ) -> list[dict[str, Any]]:
        event_dt = as_datetime(event_time)
        start_dt = event_dt - timedelta(hours=lookback_hours)
        end_dt = event_dt + timedelta(hours=lookahead_hours)
        events: list[dict[str, Any]] = []

        sources = [
            (
                "pdm_errors",
                "SELECT datetime, errorID AS event_code FROM pdm_errors "
                "WHERE machineID = ? AND datetime BETWEEN ? AND ?",
                "error",
                "source_fact",
            ),
            (
                "pdm_maint",
                "SELECT datetime, comp AS event_code FROM pdm_maint "
                "WHERE machineID = ? AND datetime BETWEEN ? AND ?",
                "maintenance",
                "source_fact",
            ),
            (
                "pdm_failures",
                "SELECT datetime, failure AS event_code FROM pdm_failures "
                "WHERE machineID = ? AND datetime BETWEEN ? AND ?",
                "failure",
                "source_fact",
            ),
        ]
        for table, sql, event_type, evidence_level in sources:
            for row in self.db.fetch_all(sql, (machine_id, start_dt, end_dt)):
                events.append(
                    {
                        "event_time": row["datetime"],
                        "event_type": event_type,
                        "event_code": row["event_code"],
                        "source_table": table,
                        "evidence_level": evidence_level,
                    }
                )

        for row in self.db.fetch_all(
            "SELECT datetime, log_type, component, operator_note, linked_fault_id "
            "FROM synthetic_operator_logs WHERE machineID = ? AND datetime BETWEEN ? AND ?",
            (machine_id, start_dt, end_dt),
        ):
            events.append(
                {
                    "event_time": row["datetime"],
                    "event_type": "operator_log",
                    "event_code": row["log_type"],
                    "component": row["component"],
                    "text": row["operator_note"],
                    "linked_fault_id": row["linked_fault_id"],
                    "source_table": "synthetic_operator_logs",
                    "evidence_level": "synthetic_text",
                }
            )

        events.sort(key=lambda item: str(item["event_time"]))
        return to_builtin(events)

    def root_cause_analysis(
        self,
        machine_id: int,
        event_time: str | datetime,
        component: str | None = None,
        top_k: int = 3,
    ) -> dict[str, Any]:
        event_dt = as_datetime(event_time)
        params: list[Any] = [machine_id, event_dt]
        sql = "SELECT * FROM synthetic_fault_reports WHERE machineID = ? AND datetime = ?"
        if component:
            sql += " AND component = ?"
            params.append(component)
        sql += " ORDER BY component"
        report = self.db.fetch_one(sql, params)
        if report and not component:
            component = str(report["component"])

        machine = self.db.fetch_one(
            "SELECT m.model AS model_code, d.display_name_zh AS model_name, m.age "
            "FROM pdm_machines m JOIN dim_machine_models d ON d.source_model_code=m.model "
            "WHERE m.machineID = ?",
            (machine_id,),
        )
        if not machine:
            raise ValueError(f"machineID={machine_id} does not exist")

        start_72h = event_dt - timedelta(hours=72)
        start_30d = event_dt - timedelta(days=30)
        errors = self.db.fetch_all(
            "SELECT datetime, errorID FROM pdm_errors "
            "WHERE machineID = ? AND datetime >= ? AND datetime < ? ORDER BY datetime",
            (machine_id, start_72h, event_dt),
        )
        maint_sql = (
            "SELECT datetime, comp FROM pdm_maint "
            "WHERE machineID = ? AND datetime >= ? AND datetime < ?"
        )
        maint_params: list[Any] = [machine_id, start_30d, event_dt]
        if component:
            maint_sql += " AND comp = ?"
            maint_params.append(component)
        maint_sql += " ORDER BY datetime"
        maintenance = self.db.fetch_all(maint_sql, maint_params)

        if report:
            feature_z = {
                metric: {
                    "last_z": float(report[f"{metric}_last_z"]),
                    "max_abs_z": abs(float(report[f"{metric}_max_abs_z"])),
                    "trend_per_hour": float(report[f"{metric}_trend_per_hour"]),
                    "last": float(report[f"{metric}_last"]),
                    "mean": float(report[f"{metric}_mean"]),
                }
                for metric in METRICS
            }
            dominant_metric = str(report["dominant_metric"])
            telemetry_points = int(report["telemetry_points_72h"])
            synthetic_reference_label = str(report["root_cause"])
            fault_id = str(report["fault_id"])
        else:
            telemetry = self.db.query_df(
                "SELECT datetime, volt, rotate, pressure, vibration FROM pdm_telemetry "
                "WHERE machineID = ? AND datetime >= ? AND datetime < ? ORDER BY datetime",
                (machine_id, start_72h, event_dt),
            )
            if telemetry.empty:
                raise ValueError("No pre-event telemetry is available for this scope")
            feature_z = {}
            for metric in METRICS:
                values = pd.to_numeric(telemetry[metric], errors="coerce").dropna()
                mean = float(values.mean())
                std = float(values.std(ddof=0))
                last = float(values.iloc[-1])
                last_z = (last - mean) / std if std else 0.0
                max_abs_z = float(((values - mean) / std).abs().max()) if std else 0.0
                trend = float(np.polyfit(np.arange(len(values)), values, 1)[0]) if len(values) > 1 else 0.0
                feature_z[metric] = {
                    "last_z": last_z,
                    "max_abs_z": max_abs_z,
                    "trend_per_hour": trend,
                    "last": last,
                    "mean": mean,
                }
            dominant_metric = max(METRICS, key=lambda metric: feature_z[metric]["max_abs_z"])
            telemetry_points = len(telemetry)
            synthetic_reference_label = None
            fault_id = None

        matching_rules = [
            item
            for item in self.rules["candidates"]
            if not component or component in item["components"]
        ]
        if not matching_rules:
            raise ValueError(f"No RCA rules found for component={component!r}")

        weights = self.rules["weights"]
        age = int(machine["age"])
        candidates = []
        for item in matching_rules:
            component_affinity = 1.0 if component in item["components"] else 0.25
            metric_fit = 1.0 if dominant_metric in item["metrics"] else 0.25
            relevant_strength = max(feature_z[m]["max_abs_z"] for m in item["metrics"])
            anomaly_strength = min(relevant_strength / 4.0, 1.0)

            if item["error_pattern"] == "present":
                error_signal = min(len(errors) / 3.0, 1.0)
            elif item["error_pattern"] == "absent":
                error_signal = max(0.0, 1.0 - len(errors) / 3.0)
            else:
                error_signal = 0.5

            if item["maintenance_pattern"] == "recent":
                maintenance_signal = 1.0 if maintenance else 0.2
            elif item["maintenance_pattern"] == "absent":
                maintenance_signal = 1.0 if not maintenance else 0.3
            else:
                maintenance_signal = 0.5

            normalized_age = min(max(age / 20.0, 0.0), 1.0)
            if item["age_pattern"] == "old":
                age_signal = normalized_age
            elif item["age_pattern"] == "young":
                age_signal = 1.0 - normalized_age
            else:
                age_signal = 0.5

            temporal_precedence = 1.0 if telemetry_points > 0 else 0.0
            score_parts = {
                "component_affinity": component_affinity,
                "metric_fit": metric_fit,
                "anomaly_strength": anomaly_strength,
                "error_signal": error_signal,
                "maintenance_signal": maintenance_signal,
                "age_signal": age_signal,
                "temporal_precedence": temporal_precedence,
            }
            score = 100 * sum(weights[key] * value for key, value in score_parts.items())
            candidates.append(
                {
                    "candidate_name": item["name"],
                    "confidence_score": round(score, 3),
                    "mechanism": item["mechanism"],
                    "recommended_action": item["recommended_action"],
                    "score_breakdown": {key: round(value, 4) for key, value in score_parts.items()},
                    "supporting_evidence": {
                        "component": component,
                        "dominant_metric": dominant_metric,
                        "relevant_max_abs_z": round(relevant_strength, 6),
                        "pre_event_error_count_72h": len(errors),
                        "pre_event_error_codes_72h": sorted({row["errorID"] for row in errors}),
                        "same_component_maintenance_count_30d": len(maintenance),
                        "machine_model_code": machine["model_code"],
                        "machine_model_name": machine["model_name"],
                        "machine_age": age,
                        "telemetry_points": telemetry_points,
                    },
                    "limitations": RCA_LIMITATION,
                }
            )

        candidates.sort(key=lambda item: item["confidence_score"], reverse=True)
        for rank, candidate in enumerate(candidates, 1):
            candidate["rank"] = rank

        result = {
            "machineID": machine_id,
            "event_time": event_dt,
            "component": component,
            "fault_id": fault_id,
            "evidence_window": {
                "telemetry_and_errors": [start_72h, event_dt],
                "same_component_maintenance": [start_30d, event_dt],
                "post_event_data_used": False,
            },
            "observed_features": feature_z,
            "actual_error_events": errors,
            "actual_maintenance_events": maintenance,
            "synthetic_reference_label": synthetic_reference_label,
            "synthetic_reference_used_in_score": False,
            "candidates": candidates[:top_k],
            "conclusion_policy": RCA_LIMITATION,
        }
        return to_builtin(result)

    def persist_anomalies(self, run_id: str, anomalies: list[dict[str, Any]]) -> None:
        rows = []
        for item in anomalies:
            rows.append(
                (
                    item["anomaly_id"],
                    run_id,
                    item["machineID"],
                    item["event_time"],
                    item["metric"],
                    item["actual_value"],
                    item["baseline_value"],
                    item["robust_z"],
                    item["direction"],
                    item["severity"],
                    item["method"],
                    json.dumps(item["evidence"], ensure_ascii=False),
                )
            )
        if rows:
            self.db.executemany(
                "INSERT INTO detected_anomalies "
                "(anomaly_id, run_id, machineID, event_time, metric, actual_value, baseline_value, "
                "robust_z, direction, severity, method, evidence_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def persist_rca(self, run_id: str, rca: dict[str, Any]) -> None:
        rows = []
        for candidate in rca["candidates"]:
            evidence = {
                "score_breakdown": candidate["score_breakdown"],
                "supporting_evidence": candidate["supporting_evidence"],
                "mechanism": candidate["mechanism"],
                "recommended_action": candidate["recommended_action"],
                "synthetic_reference_used_in_score": False,
            }
            rows.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    rca.get("fault_id"),
                    None,
                    candidate["rank"],
                    candidate["candidate_name"],
                    candidate["confidence_score"],
                    json.dumps(evidence, ensure_ascii=False),
                    candidate["limitations"],
                )
            )
        self.db.executemany(
            "INSERT INTO root_cause_candidates "
            "(candidate_id, run_id, fault_id, anomaly_id, candidate_rank, candidate_name, "
            "confidence_score, evidence_json, limitations_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
