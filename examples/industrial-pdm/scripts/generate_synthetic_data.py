#!/usr/bin/env python3
"""Build deterministic, provenance-rich demo data from the five source CSV files.

The source files are never modified. Every row emitted by this script is explicitly
labelled as synthetic/derived and is linked back to one or more source events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "original"
OUTPUT_DIR = ROOT / "synthetic"
GENERATOR_VERSION = "2.1.0"
METRICS = ("volt", "rotate", "pressure", "vibration")

EXPECTED_SHA256 = {
    "PdM_telemetry.csv": "d957f3c45bb83416b716600da8cffd72f4b6961db89867d9696ad19f7cb1bd4e",
    "PdM_errors.csv": "9c2a2a010ad77227e2bb0c94e7971bca78810790ddd1f28a8bee4f12c2f62370",
    "PdM_maint.csv": "481ed4e155f609e6ca6130754d2c035453093902a507cce5b3f3e235995f1db6",
    "PdM_failures.csv": "0c6c31a4fd52ef2df95ad7c44e8b0c8c32917bcef29ba5a1ba3ba45531ded3b7",
    "PdM_machines.csv": "5e8e1571c4999bf88abb7cae3925964c218d946fe851a9a100bb3d19330652bc",
}

COMPONENT_ACTIONS = {
    "comp1": "检查轴承、润滑和安装同心度，完成复紧或更换后复测振动与转速",
    "comp2": "检查负载、冷却回路和密封状态，处理异常后复测压力与电气负荷",
    "comp3": "检查端子、线束、传感器和控制参数，校准后复测电压稳定性",
    "comp4": "检查疲劳、对中和机械紧固状态，处理后执行空载及带载复测",
}

COMPONENT_TEAMS = {
    "comp1": "机械一组",
    "comp2": "动力二组",
    "comp3": "电仪组",
    "comp4": "机械二组",
}

PART_CATALOG = {
    "comp1": [
        ("P-C1-BEARING", "轴承组件", 680.0),
        ("P-C1-LUBE", "工业润滑剂", 95.0),
    ],
    "comp2": [
        ("P-C2-SEAL", "密封组件", 420.0),
        ("P-C2-FILTER", "冷却过滤器", 180.0),
    ],
    "comp3": [
        ("P-C3-SENSOR", "传感器组件", 760.0),
        ("P-C3-CONNECTOR", "工业接插件", 120.0),
    ],
    "comp4": [
        ("P-C4-COUPLING", "联轴器组件", 920.0),
        ("P-C4-FASTENER", "高强度紧固件包", 150.0),
    ],
}

ERROR_COMPONENT = {
    "error1": "comp1",
    "error2": "comp2",
    "error3": "comp3",
    "error4": "comp4",
    "error5": "comp4",
}

ERROR_METRIC = {
    "error1": "vibration",
    "error2": "pressure",
    "error3": "volt",
    "error4": "rotate",
    "error5": "vibration",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_unit(*parts: Any) -> float:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    value = int(hashlib.sha256(payload).hexdigest()[:15], 16)
    return value / float(0xFFFFFFFFFFFFFFF)


def fmt_dt(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def load_sources(allow_source_drift: bool) -> dict[str, pd.DataFrame]:
    actual_hashes: dict[str, str] = {}
    for name, expected in EXPECTED_SHA256.items():
        path = SOURCE_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Missing source file: {path}")
        actual = sha256(path)
        actual_hashes[name] = actual
        if actual != expected and not allow_source_drift:
            raise ValueError(
                f"Source checksum mismatch for {name}: expected {expected}, got {actual}. "
                "Use --allow-source-drift only after reviewing the source change."
            )

    frames = {
        "machines": pd.read_csv(SOURCE_DIR / "PdM_machines.csv"),
        "telemetry": pd.read_csv(SOURCE_DIR / "PdM_telemetry.csv", parse_dates=["datetime"]),
        "errors": pd.read_csv(SOURCE_DIR / "PdM_errors.csv", parse_dates=["datetime"]),
        "maint": pd.read_csv(SOURCE_DIR / "PdM_maint.csv", parse_dates=["datetime"]),
        "failures": pd.read_csv(SOURCE_DIR / "PdM_failures.csv", parse_dates=["datetime"]),
    }
    for name in ("telemetry", "errors", "maint", "failures"):
        frames[name] = frames[name].sort_values(["machineID", "datetime"]).reset_index(drop=True)
    frames["source_hashes"] = actual_hashes  # type: ignore[assignment]
    return frames


def metric_features(window: pd.DataFrame) -> dict[str, dict[str, float]]:
    features: dict[str, dict[str, float]] = {}
    for metric in METRICS:
        values = pd.to_numeric(window[metric], errors="coerce").dropna().astype(float)
        if values.empty:
            raise ValueError(f"No values available for metric={metric}")
        mean = float(values.mean())
        std = float(values.std(ddof=0))
        last = float(values.iloc[-1])
        if std:
            z_values = (values - mean) / std
            last_z = float(z_values.iloc[-1])
            max_index = z_values.abs().idxmax()
            max_abs_z = float(abs(z_values.loc[max_index]))
        else:
            last_z = 0.0
            max_abs_z = 0.0
        trend = float(np.polyfit(np.arange(len(values)), values, 1)[0]) if len(values) > 1 else 0.0
        deviation = ((last - mean) / mean * 100.0) if mean else 0.0
        features[metric] = {
            "mean": mean,
            "min": float(values.min()),
            "max": float(values.max()),
            "last": last,
            "trend_per_hour": trend,
            "last_deviation_pct": deviation,
            "last_z": last_z,
            "max_abs_z": max_abs_z,
        }
    return features


def score_rules(
    rules: dict[str, Any],
    component: str,
    dominant_metric: str,
    features: dict[str, dict[str, float]],
    error_count: int,
    recent_maintenance_count: int,
    machine_age: int,
) -> list[dict[str, Any]]:
    weights = rules["weights"]
    ranked: list[dict[str, Any]] = []
    for item in rules["candidates"]:
        if component not in item["components"]:
            continue
        metric_fit = 1.0 if dominant_metric in item["metrics"] else 0.25
        anomaly_strength = min(max(features[m]["max_abs_z"] for m in item["metrics"]) / 4.0, 1.0)
        if item["error_pattern"] == "present":
            error_signal = min(error_count / 3.0, 1.0)
        elif item["error_pattern"] == "absent":
            error_signal = max(0.0, 1.0 - error_count / 3.0)
        else:
            error_signal = 0.5
        if item["maintenance_pattern"] == "recent":
            maintenance_signal = 1.0 if recent_maintenance_count else 0.2
        elif item["maintenance_pattern"] == "absent":
            maintenance_signal = 1.0 if not recent_maintenance_count else 0.3
        else:
            maintenance_signal = 0.5
        normalized_age = min(max(machine_age / 20.0, 0.0), 1.0)
        if item["age_pattern"] == "old":
            age_signal = normalized_age
        elif item["age_pattern"] == "young":
            age_signal = 1.0 - normalized_age
        else:
            age_signal = 0.5
        parts = {
            "component_affinity": 1.0,
            "metric_fit": metric_fit,
            "anomaly_strength": anomaly_strength,
            "error_signal": error_signal,
            "maintenance_signal": maintenance_signal,
            "age_signal": age_signal,
            "temporal_precedence": 1.0,
        }
        score = 100.0 * sum(float(weights[name]) * value for name, value in parts.items())
        ranked.append({**item, "score": round(score, 6), "score_breakdown": parts})
    return sorted(ranked, key=lambda item: (-item["score"], item["name"]))


def build_fault_reports(
    frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
    model_names: dict[str, str],
) -> pd.DataFrame:
    machines = frames["machines"].set_index("machineID")
    telemetry_by_machine = {
        int(machine_id): group.reset_index(drop=True)
        for machine_id, group in frames["telemetry"].groupby("machineID", sort=False)
    }
    errors_by_machine = {
        int(machine_id): group.reset_index(drop=True)
        for machine_id, group in frames["errors"].groupby("machineID", sort=False)
    }
    maint_by_machine = {
        int(machine_id): group.reset_index(drop=True)
        for machine_id, group in frames["maint"].groupby("machineID", sort=False)
    }
    rows: list[dict[str, Any]] = []
    failures = frames["failures"].sort_values(["datetime", "machineID", "failure"]).reset_index(drop=True)
    for index, failure in failures.iterrows():
        machine_id = int(failure["machineID"])
        component = str(failure["failure"])
        event_time = pd.Timestamp(failure["datetime"])
        start_72h = event_time - pd.Timedelta(hours=72)
        telemetry = telemetry_by_machine[machine_id]
        window = telemetry[(telemetry["datetime"] >= start_72h) & (telemetry["datetime"] < event_time)]
        if window.empty:
            raise ValueError(f"No pre-failure telemetry for machine={machine_id}, time={event_time}")
        features = metric_features(window)
        dominant_metric = max(METRICS, key=lambda metric: features[metric]["max_abs_z"])

        machine_errors = errors_by_machine.get(machine_id, pd.DataFrame(columns=frames["errors"].columns))
        nearby_errors = machine_errors[
            (machine_errors["datetime"] >= start_72h) & (machine_errors["datetime"] < event_time)
        ]
        machine_maint = maint_by_machine.get(machine_id, pd.DataFrame(columns=frames["maint"].columns))
        recent_maint = machine_maint[
            (machine_maint["datetime"] >= event_time - pd.Timedelta(days=30))
            & (machine_maint["datetime"] < event_time)
            & (machine_maint["comp"] == component)
        ]
        exact_maint = machine_maint[
            (machine_maint["datetime"] == event_time) & (machine_maint["comp"] == component)
        ]
        machine = machines.loc[machine_id]
        ranked = score_rules(
            rules,
            component,
            dominant_metric,
            features,
            len(nearby_errors),
            len(recent_maint),
            int(machine["age"]),
        )
        winner = ranked[0]
        max_z = max(item["max_abs_z"] for item in features.values())
        severity = "high" if max_z >= 3.5 or len(nearby_errors) >= 3 else "medium" if max_z >= 2 or len(nearby_errors) else "low"
        error_codes = sorted(set(str(value) for value in nearby_errors.get("errorID", [])))
        if recent_maint.empty:
            recent_maint_text = "故障前30天无同部件来源维护事件"
            recent_maint_last = ""
        else:
            last_maint = pd.Timestamp(recent_maint.iloc[-1]["datetime"])
            recent_maint_text = f"故障前30天有{len(recent_maint)}次同部件来源维护，最近一次为{fmt_dt(last_maint)}"
            recent_maint_last = fmt_dt(last_maint)
        dominant = features[dominant_metric]
        source_model_code = str(machine["model"])
        display_model_name = model_names.get(source_model_code, source_model_code)
        report_text = (
            f"合成分析报告：设备{machine_id}（{display_model_name}，来源编码 {source_model_code}，"
            f"机龄{int(machine['age'])}年）于"
            f"{fmt_dt(event_time)}出现来源数据中的{component}故障事件。故障前窗口包含{len(window)}条遥测，"
            f"主导指标为{dominant_metric}，窗口最大标准化偏差约{dominant['max_abs_z']:.2f}σ，"
            f"最近值相对窗口均值偏差{dominant['last_deviation_pct']:.2f}%。前72小时来源错误事件"
            f"{len(nearby_errors)}条（{','.join(error_codes) if error_codes else '无'}）；{recent_maint_text}。"
            f"规则候选首位为“{winner['name']}”，建议{winner['recommended_action']}。"
            "本段由来源结构化事件确定性生成，不是现场报告，候选根因须由检修证据确认。"
        )
        row: dict[str, Any] = {
            "fault_id": f"F{index + 1:06d}",
            "datetime": fmt_dt(event_time),
            "machineID": machine_id,
            "component": component,
            "machine_model": source_model_code,
            "machine_age": int(machine["age"]),
            "dominant_metric": dominant_metric,
            "dominant_z": round(float(dominant["max_abs_z"]), 6),
            "nearby_error_count_72h": int(len(nearby_errors)),
            "nearby_error_codes_72h": ",".join(error_codes),
            "recent_same_component_maint": recent_maint_text,
            "root_cause": winner["name"],
            "failure_mechanism": winner["mechanism"],
            "severity": severity,
            "resolution": winner["recommended_action"],
            "fault_report": report_text,
            "synthetic_text_flag": 1,
            "aligned_to_source_failure": 1,
            "source_failure_key": f"{machine_id}|{fmt_dt(event_time)}|{component}",
            "generator_version": GENERATOR_VERSION,
            "telemetry_window_start": fmt_dt(window.iloc[0]["datetime"]),
            "telemetry_window_end": fmt_dt(window.iloc[-1]["datetime"]),
            "telemetry_points_72h": int(len(window)),
            "recent_same_component_maint_count_30d": int(len(recent_maint)),
            "recent_same_component_maint_last_datetime": recent_maint_last,
            "source_maintenance_exact_match": int(not exact_maint.empty),
        }
        for metric in METRICS:
            for suffix, value in features[metric].items():
                row[f"{metric}_{suffix}"] = round(float(value), 6)
        rows.append(row)
    return pd.DataFrame(rows)


def failure_lookup(reports: pd.DataFrame) -> dict[int, list[tuple[pd.Timestamp, str, str]]]:
    result: dict[int, list[tuple[pd.Timestamp, str, str]]] = {}
    for row in reports.itertuples(index=False):
        result.setdefault(int(row.machineID), []).append(
            (pd.Timestamp(row.datetime), str(row.fault_id), str(row.component))
        )
    for values in result.values():
        values.sort(key=lambda item: item[0])
    return result


def nearest_future_failure(
    failures: list[tuple[pd.Timestamp, str, str]], event_time: pd.Timestamp, hours: int = 72
) -> tuple[pd.Timestamp, str, str] | None:
    times = [item[0] for item in failures]
    position = bisect_right(times, event_time)
    if position >= len(failures):
        return None
    candidate = failures[position]
    return candidate if candidate[0] <= event_time + pd.Timedelta(hours=hours) else None


def build_operator_logs(frames: dict[str, pd.DataFrame], reports: pd.DataFrame) -> pd.DataFrame:
    telemetry_by_machine = {
        int(machine_id): group.reset_index(drop=True)
        for machine_id, group in frames["telemetry"].groupby("machineID", sort=False)
    }
    errors_by_machine = {
        int(machine_id): group.reset_index(drop=True)
        for machine_id, group in frames["errors"].groupby("machineID", sort=False)
    }
    lookup = failure_lookup(reports)
    records: list[dict[str, Any]] = []

    for report in reports.itertuples(index=False):
        machine_id = int(report.machineID)
        event_time = pd.Timestamp(report.datetime)
        telemetry = telemetry_by_machine[machine_id]
        window = telemetry[
            (telemetry["datetime"] >= event_time - pd.Timedelta(hours=72))
            & (telemetry["datetime"] < event_time)
        ].copy()
        z_frame = pd.DataFrame(index=window.index)
        for metric in METRICS:
            values = window[metric].astype(float)
            std = float(values.std(ddof=0))
            z_frame[metric] = (values - float(values.mean())) / std if std else 0.0
        row_score = z_frame.abs().max(axis=1)
        take = min(len(window), 2 + int(stable_unit(report.fault_id, "precursor_count") * 3))
        for source_index in row_score.nlargest(take).sort_index().index:
            source = window.loc[source_index]
            metric = str(z_frame.loc[source_index].abs().idxmax())
            z_value = float(z_frame.loc[source_index, metric])
            direction = "高于" if z_value >= 0 else "低于"
            severity = "high" if abs(z_value) >= 3.5 else "medium" if abs(z_value) >= 2 else "low"
            hours_before = int((event_time - pd.Timestamp(source["datetime"])).total_seconds() // 3600)
            records.append(
                {
                    "datetime": fmt_dt(source["datetime"]),
                    "machineID": machine_id,
                    "component": report.component,
                    "log_type": "failure_precursor_telemetry",
                    "severity": severity,
                    "error_code": "",
                    "dominant_metric": metric,
                    "operator_note": (
                        f"合成巡检记录：设备{machine_id}在{fmt_dt(source['datetime'])}的{metric}为"
                        f"{float(source[metric]):.3f}，{direction}该故障前窗口均值，标准化偏差{z_value:.2f}σ；"
                        f"距来源{report.component}故障约{hours_before}小时。该文字由遥测计算生成，不是现场人员原始记录。"
                    ),
                    "linked_fault_id": report.fault_id,
                    "synthetic_text_flag": 1,
                    "aligned_to_source_failure": 1,
                    "source_event_type": "telemetry",
                    "source_event_key": f"{machine_id}|{fmt_dt(source['datetime'])}",
                    "generator_version": GENERATOR_VERSION,
                }
            )

    for error in frames["errors"].itertuples(index=False):
        machine_id = int(error.machineID)
        event_time = pd.Timestamp(error.datetime)
        linked = nearest_future_failure(lookup.get(machine_id, []), event_time)
        linked_fault_id = linked[1] if linked else ""
        component = linked[2] if linked else ERROR_COMPONENT.get(str(error.errorID), "all")
        records.append(
            {
                "datetime": fmt_dt(event_time),
                "machineID": machine_id,
                "component": component,
                "log_type": "source_error_narrative",
                "severity": "medium" if linked else "low",
                "error_code": str(error.errorID),
                "dominant_metric": ERROR_METRIC.get(str(error.errorID), "vibration"),
                "operator_note": (
                    f"合成错误叙述：来源错误表记录设备{machine_id}于{fmt_dt(event_time)}发生{error.errorID}。"
                    + (
                        f"该事件位于来源{component}故障前72小时内，关联故障编号{linked_fault_id}。"
                        if linked
                        else "其后72小时内未出现来源故障事件，作为错误背景样本保留。"
                    )
                    + "本段是对结构化错误事件的文字化，不是现场原始日志。"
                ),
                "linked_fault_id": linked_fault_id,
                "synthetic_text_flag": 1,
                "aligned_to_source_failure": int(linked is not None),
                "source_event_type": "error",
                "source_event_key": f"{machine_id}|{fmt_dt(event_time)}|{error.errorID}",
                "generator_version": GENERATOR_VERSION,
            }
        )

    quarter_targets = ("02-15 12:00:00", "05-15 12:00:00", "08-15 12:00:00", "11-15 12:00:00")
    for machine_id, telemetry in telemetry_by_machine.items():
        failure_times = [item[0] for item in lookup.get(machine_id, [])]
        error_times = list(errors_by_machine.get(machine_id, pd.DataFrame()).get("datetime", []))
        for quarter in quarter_targets:
            target = pd.Timestamp(f"2015-{quarter}")
            candidates = telemetry.iloc[(telemetry["datetime"] - target).abs().argsort()]
            chosen = None
            for candidate in candidates.itertuples(index=False):
                candidate_time = pd.Timestamp(candidate.datetime)
                if any(abs((candidate_time - value).total_seconds()) <= 72 * 3600 for value in failure_times):
                    continue
                if any(abs((candidate_time - pd.Timestamp(value)).total_seconds()) <= 24 * 3600 for value in error_times):
                    continue
                chosen = candidate
                break
            if chosen is None:
                continue
            chosen_time = pd.Timestamp(chosen.datetime)
            baseline = telemetry[
                (telemetry["datetime"] >= chosen_time - pd.Timedelta(hours=72))
                & (telemetry["datetime"] < chosen_time)
            ]
            if baseline.empty:
                continue
            z_values: dict[str, float] = {}
            for metric in METRICS:
                std = float(baseline[metric].std(ddof=0))
                z_values[metric] = (
                    (float(getattr(chosen, metric)) - float(baseline[metric].mean())) / std if std else 0.0
                )
            metric = max(METRICS, key=lambda name: abs(z_values[name]))
            records.append(
                {
                    "datetime": fmt_dt(chosen_time),
                    "machineID": machine_id,
                    "component": "all",
                    "log_type": "normal_inspection_background",
                    "severity": "info",
                    "error_code": "",
                    "dominant_metric": metric,
                    "operator_note": (
                        f"合成正常巡检：设备{machine_id}于{fmt_dt(chosen_time)}的四项遥测均接近此前72小时基线，"
                        f"最大绝对偏差为{abs(z_values[metric]):.2f}σ（{metric}）；前后窗口无邻近故障或错误。"
                        "本条用于形成非故障对照，不是现场原始日志。"
                    ),
                    "linked_fault_id": "",
                    "synthetic_text_flag": 1,
                    "aligned_to_source_failure": 0,
                    "source_event_type": "telemetry_normal_sample",
                    "source_event_key": f"{machine_id}|{fmt_dt(chosen_time)}",
                    "generator_version": GENERATOR_VERSION,
                }
            )

    logs = pd.DataFrame(records).sort_values(
        ["datetime", "machineID", "log_type", "source_event_key", "linked_fault_id"]
    ).reset_index(drop=True)
    logs.insert(0, "log_id", [f"LOG{index:07d}" for index in range(1, len(logs) + 1)])
    return logs


def build_maintenance_notes(frames: dict[str, pd.DataFrame], reports: pd.DataFrame) -> pd.DataFrame:
    report_by_key = {
        (int(row.machineID), fmt_dt(row.datetime), str(row.component)): row
        for row in reports.itertuples(index=False)
    }
    covered_faults: set[str] = set()
    rows: list[dict[str, Any]] = []
    for maint in frames["maint"].itertuples(index=False):
        machine_id = int(maint.machineID)
        event_time = pd.Timestamp(maint.datetime)
        component = str(maint.comp)
        linked = report_by_key.get((machine_id, fmt_dt(event_time), component))
        if linked:
            covered_faults.add(str(linked.fault_id))
            maintenance_type = "source_reactive_replacement_with_synthetic_note"
            root_cause = str(linked.root_cause)
            linked_fault_id = str(linked.fault_id)
            aligned = 1
            exact = 1
            context = f"同一时刻存在来源{component}故障，规则候选首位为“{root_cause}”"
        else:
            maintenance_type = "source_preventive_replacement_with_synthetic_note"
            root_cause = "预防性更换（无现场根因）"
            linked_fault_id = ""
            aligned = 0
            exact = 0
            context = "该来源维护事件未与同一时刻、同一部件的故障记录完全匹配"
        action = COMPONENT_ACTIONS[component]
        rows.append(
            {
                "datetime": fmt_dt(event_time),
                "machineID": machine_id,
                "component": component,
                "maintenance_type": maintenance_type,
                "root_cause": root_cause,
                "action_taken": action,
                "technician_note": (
                    f"合成维修说明：来源维护表记录设备{machine_id}于{fmt_dt(event_time)}更换{component}；{context}。"
                    f"演示动作建议为：{action}。原因、动作、人员和成本均为演示衍生内容，不代表现场实际执行。"
                ),
                "linked_fault_id": linked_fault_id,
                "synthetic_text_flag": 1,
                "aligned_to_source_failure": aligned,
                "source_maintenance_exact_match": exact,
                "source_event_key": f"{machine_id}|{fmt_dt(event_time)}|{component}",
                "generator_version": GENERATOR_VERSION,
            }
        )

    for report in reports.itertuples(index=False):
        if str(report.fault_id) in covered_faults:
            continue
        event_time = pd.Timestamp(report.datetime) + pd.Timedelta(hours=2)
        action = COMPONENT_ACTIONS[str(report.component)]
        rows.append(
            {
                "datetime": fmt_dt(event_time),
                "machineID": int(report.machineID),
                "component": str(report.component),
                "maintenance_type": "synthetic_failure_followup_without_source_maintenance",
                "root_cause": str(report.root_cause),
                "action_taken": action,
                "technician_note": (
                    f"合成跟进建议：来源数据在{fmt_dt(report.datetime)}记录设备{report.machineID}的"
                    f"{report.component}故障，但没有同一时刻、同一部件的来源维护事件。为保证演示流程完整，"
                    f"生成待执行建议“{action}”。本条不是实际维修记录。"
                ),
                "linked_fault_id": str(report.fault_id),
                "synthetic_text_flag": 1,
                "aligned_to_source_failure": 1,
                "source_maintenance_exact_match": 0,
                "source_event_key": f"{report.fault_id}|synthetic_followup",
                "generator_version": GENERATOR_VERSION,
            }
        )

    notes = pd.DataFrame(rows).sort_values(
        ["datetime", "machineID", "component", "maintenance_type"]
    ).reset_index(drop=True)
    notes.insert(0, "maint_note_id", [f"MN{index:07d}" for index in range(1, len(notes) + 1)])
    return notes


def build_work_orders_and_parts(
    notes: pd.DataFrame, machines: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    age_by_machine = machines.set_index("machineID")["age"].to_dict()
    work_orders: list[dict[str, Any]] = []
    parts_rows: list[dict[str, Any]] = []
    for index, note in enumerate(notes.itertuples(index=False), 1):
        work_order_id = f"WO{index:07d}"
        event_time = pd.Timestamp(note.datetime)
        linked = bool(str(note.linked_fault_id).strip())
        exact_maint = int(note.source_maintenance_exact_match) == 1
        work_type = "corrective" if linked else "preventive"
        priority = "critical" if linked and stable_unit(work_order_id, "priority") > 0.72 else "high" if linked else "medium"
        base_duration = {"comp1": 4.0, "comp2": 5.0, "comp3": 3.0, "comp4": 6.0}[str(note.component)]
        duration = round(
            base_duration
            + float(age_by_machine[int(note.machineID)]) * 0.08
            + stable_unit(work_order_id, "duration") * 3.0,
            2,
        )
        opened_at = event_time - pd.Timedelta(hours=1 if linked else 4)
        closed_at = event_time + pd.Timedelta(hours=duration)
        downtime = round(duration * (0.78 if linked else 0.42), 2)
        labor_hours = round(duration * (2.0 if linked else 1.4), 2)
        labor_cost = round(labor_hours * (110.0 + 20.0 * stable_unit(work_order_id, "labor")), 2)

        selected_parts = [PART_CATALOG[str(note.component)][0]]
        if stable_unit(work_order_id, "second_part") > 0.35:
            selected_parts.append(PART_CATALOG[str(note.component)][1])
        parts_cost = 0.0
        for part_index, (part_code, part_name, unit_cost) in enumerate(selected_parts, 1):
            quantity = 1 + int(stable_unit(work_order_id, part_code) > 0.82)
            total_cost = round(quantity * unit_cost, 2)
            parts_cost += total_cost
            parts_rows.append(
                {
                    "usage_id": f"PU{index:07d}-{part_index}",
                    "work_order_id": work_order_id,
                    "part_code": part_code,
                    "part_name": part_name,
                    "quantity": quantity,
                    "unit_cost": round(unit_cost, 2),
                    "total_cost": total_cost,
                    "synthetic_record_flag": 1,
                    "generator_version": GENERATOR_VERSION,
                }
            )
        parts_cost = round(parts_cost, 2)
        work_orders.append(
            {
                "work_order_id": work_order_id,
                "opened_at": fmt_dt(opened_at),
                "closed_at": fmt_dt(closed_at),
                "machineID": int(note.machineID),
                "component": str(note.component),
                "work_order_type": work_type,
                "priority": priority,
                "status": "closed",
                "assigned_team": COMPONENT_TEAMS[str(note.component)],
                "duration_hours": duration,
                "downtime_hours": downtime,
                "labor_hours": labor_hours,
                "labor_cost": labor_cost,
                "parts_cost": parts_cost,
                "total_cost": round(labor_cost + parts_cost, 2),
                "linked_fault_id": str(note.linked_fault_id),
                "linked_maint_note_id": str(note.maint_note_id),
                "source_maintenance_event_flag": int(exact_maint),
                "synthetic_record_flag": 1,
                "generator_version": GENERATOR_VERSION,
            }
        )
    return pd.DataFrame(work_orders), pd.DataFrame(parts_rows)


def build_daily_operations(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    telemetry = frames["telemetry"].copy()
    telemetry = telemetry[
        (telemetry["datetime"] >= pd.Timestamp("2015-01-01"))
        & (telemetry["datetime"] < pd.Timestamp("2016-01-01"))
    ]
    telemetry["operation_date"] = telemetry["datetime"].dt.strftime("%Y-%m-%d")
    daily = (
        telemetry.groupby(["operation_date", "machineID"], as_index=False)
        .agg(
            source_telemetry_points=("datetime", "size"),
            avg_volt=("volt", "mean"),
            avg_rotate=("rotate", "mean"),
            avg_pressure=("pressure", "mean"),
            avg_vibration=("vibration", "mean"),
        )
    )
    machine_baseline = (
        telemetry.groupby("machineID", as_index=False)
        .agg(machine_avg_vibration=("vibration", "mean"), machine_std_vibration=("vibration", "std"))
    )
    daily = daily.merge(frames["machines"], on="machineID", how="left").merge(
        machine_baseline, on="machineID", how="left"
    )

    def event_counts(frame: pd.DataFrame, column_name: str) -> pd.DataFrame:
        copy = frame.copy()
        copy["operation_date"] = copy["datetime"].dt.strftime("%Y-%m-%d")
        return copy.groupby(["operation_date", "machineID"], as_index=False).size().rename(columns={"size": column_name})

    daily = daily.merge(event_counts(frames["errors"], "source_error_count"), on=["operation_date", "machineID"], how="left")
    daily = daily.merge(event_counts(frames["failures"], "source_failure_count"), on=["operation_date", "machineID"], how="left")
    daily = daily.merge(event_counts(frames["maint"], "source_maintenance_count"), on=["operation_date", "machineID"], how="left")
    for column in ("source_error_count", "source_failure_count", "source_maintenance_count"):
        daily[column] = daily[column].fillna(0).astype(int)

    vibration_z = (
        (daily["avg_vibration"] - daily["machine_avg_vibration"])
        / daily["machine_std_vibration"].replace(0, np.nan)
    ).abs().fillna(0.0)
    noise = np.array(
        [stable_unit(row.operation_date, int(row.machineID), "operations") for row in daily.itertuples(index=False)]
    )
    daily["scheduled_hours"] = 24.0
    daily["downtime_hours"] = (
        0.25
        + 0.75 * noise
        + 0.20 * daily["source_error_count"]
        + 5.00 * daily["source_failure_count"]
        + 1.20 * daily["source_maintenance_count"]
    ).clip(upper=20.0).round(3)
    daily["runtime_hours"] = (daily["scheduled_hours"] - daily["downtime_hours"]).round(3)
    base_rate = daily["model"].map({"model1": 112.0, "model2": 106.0, "model3": 101.0, "model4": 96.0}).fillna(100.0)
    daily["performance_pct"] = (
        96.0
        - 1.8 * vibration_z.clip(upper=5)
        - 4.0 * daily["source_failure_count"]
        - 0.5 * daily["source_error_count"]
        + (noise - 0.5) * 2.0
    ).clip(lower=65.0, upper=99.0).round(3)
    daily["production_units"] = (
        daily["runtime_hours"] * base_rate * daily["performance_pct"] / 100.0
    ).round().astype(int)
    scrap_rate = (
        0.012
        + 0.004 * vibration_z.clip(upper=5)
        + 0.020 * daily["source_failure_count"]
        + 0.0015 * daily["source_error_count"]
    ).clip(upper=0.18)
    daily["scrap_units"] = (daily["production_units"] * scrap_rate).round().astype(int)
    daily["good_units"] = daily["production_units"] - daily["scrap_units"]
    daily["availability_pct"] = (daily["runtime_hours"] / daily["scheduled_hours"] * 100.0).round(3)
    daily["quality_pct"] = np.where(
        daily["production_units"] > 0,
        daily["good_units"] / daily["production_units"] * 100.0,
        100.0,
    ).round(3)
    daily["oee_pct"] = (
        daily["availability_pct"] * daily["performance_pct"] * daily["quality_pct"] / 10000.0
    ).round(3)
    model_energy = daily["model"].map({"model1": 31.0, "model2": 29.0, "model3": 27.0, "model4": 25.0}).fillna(28.0)
    daily["energy_kwh"] = (
        daily["runtime_hours"] * model_energy * (1.0 + 0.03 * vibration_z.clip(upper=5))
    ).round(3)
    daily["synthetic_record_flag"] = 1
    daily["generator_version"] = GENERATOR_VERSION
    columns = [
        "operation_date",
        "machineID",
        "model",
        "age",
        "scheduled_hours",
        "runtime_hours",
        "downtime_hours",
        "production_units",
        "good_units",
        "scrap_units",
        "availability_pct",
        "performance_pct",
        "quality_pct",
        "oee_pct",
        "energy_kwh",
        "source_telemetry_points",
        "source_error_count",
        "source_failure_count",
        "source_maintenance_count",
        "synthetic_record_flag",
        "generator_version",
    ]
    return daily[columns].sort_values(["operation_date", "machineID"]).reset_index(drop=True)


def write_csv(frame: pd.DataFrame, name: str) -> None:
    path = OUTPUT_DIR / name
    frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
    print(f"wrote {name}: {len(frame):,} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic PdM demo tables")
    parser.add_argument("--allow-source-drift", action="store_true")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    frames = load_sources(args.allow_source_drift)
    rules = json.loads((ROOT / "config" / "rca_rules.json").read_text(encoding="utf-8"))
    model_catalog_path = ROOT / "config" / "model_catalog.json"
    model_catalog = json.loads(model_catalog_path.read_text(encoding="utf-8"))
    model_names = {
        item["source_model_code"]: item["display_name_zh"]
        for item in model_catalog["models"]
    }
    reports = build_fault_reports(frames, rules, model_names)
    logs = build_operator_logs(frames, reports)
    notes = build_maintenance_notes(frames, reports)
    work_orders, parts = build_work_orders_and_parts(notes, frames["machines"])
    daily_operations = build_daily_operations(frames)

    write_csv(reports, "synthetic_fault_reports.csv")
    write_csv(logs, "synthetic_operator_logs.csv")
    write_csv(notes, "synthetic_maintenance_notes.csv")
    write_csv(work_orders, "synthetic_work_orders.csv")
    write_csv(parts, "synthetic_parts_usage.csv")
    write_csv(daily_operations, "synthetic_daily_operations.csv")

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "generation_mode": "deterministic_no_random_sampling",
        "source_directory": "original",
        "source_sha256": frames["source_hashes"],
        "model_catalog_version": model_catalog["version"],
        "model_catalog_sha256": sha256(model_catalog_path),
        "tables": {
            "synthetic_fault_reports": len(reports),
            "synthetic_operator_logs": len(logs),
            "synthetic_maintenance_notes": len(notes),
            "synthetic_work_orders": len(work_orders),
            "synthetic_parts_usage": len(parts),
            "synthetic_daily_operations": len(daily_operations),
        },
        "evidence_boundary": (
            "All files in synthetic/ are derived demo data. They are not field observations, "
            "operator-authored logs, confirmed root causes, executed work orders, or financial records."
        ),
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["tables"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
