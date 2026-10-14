from __future__ import annotations

import html
import json
import uuid
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

from .analyzer import to_builtin


def _save_monthly_chart(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(frame["month"], frame["failure_count"], marker="o", linewidth=2, color="#2563eb")
    ax.set_title("Monthly failure events")
    ax.set_xlabel("Month")
    ax.set_ylabel("Count")
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _save_component_chart(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame(rows).sort_values("failure_count")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.barh(frame["component"], frame["failure_count"], color="#0f766e")
    ax.set_title("Failures by component")
    ax.set_xlabel("Count")
    ax.grid(axis="x", alpha=0.25)
    for index, value in enumerate(frame["failure_count"]):
        ax.text(value + 2, index, str(int(value)), va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _save_machine_chart(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame(rows).sort_values("failure_count")
    labels = [f"M{int(value)}" for value in frame["machineID"]]
    fig, ax = plt.subplots(figsize=(8, 5.2))
    ax.barh(labels, frame["failure_count"], color="#d97706")
    ax.set_title("Top machines by failure count")
    ax.set_xlabel("Failure count")
    ax.grid(axis="x", alpha=0.25)
    for index, value in enumerate(frame["failure_count"]):
        ax.text(value + 0.15, index, str(int(value)), va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _save_work_order_cost_chart(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame(rows).sort_values("total_cost")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.barh(frame["component"], frame["total_cost"], color="#7c3aed")
    ax.set_title("Synthetic work-order cost by component")
    ax.set_xlabel("Cost")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _save_oee_chart(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame(rows).sort_values("avg_oee_pct")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.barh(frame["model_name"], frame["avg_oee_pct"], color="#0891b2")
    ax.set_title("Synthetic average OEE by model")
    ax.set_xlabel("OEE (%)")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(value))}</td>" for value in row)
        body.append(f"<tr>{cells}</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def build_report(
    snapshot: dict[str, Any],
    output_dir: str | Path,
    rca: dict[str, Any] | None = None,
    anomalies: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    monthly_chart = figures_dir / "failures_by_month.png"
    component_chart = figures_dir / "failures_by_component.png"
    machine_chart = figures_dir / "top_failure_machines.png"
    work_order_chart = figures_dir / "synthetic_work_order_cost.png"
    oee_chart = figures_dir / "synthetic_oee_by_model.png"
    _save_monthly_chart(snapshot["monthly_failures"], monthly_chart)
    _save_component_chart(snapshot["failure_by_component"], component_chart)
    _save_machine_chart(snapshot["top_machines"], machine_chart)
    _save_work_order_cost_chart(snapshot["synthetic_work_orders_by_component"], work_order_chart)
    _save_oee_chart(snapshot["synthetic_oee_by_model"], oee_chart)

    counts = snapshot["counts"]
    report_id = str(uuid.uuid4())
    work_order_summary = snapshot["synthetic_work_order_summary"]
    operations_summary = snapshot["synthetic_operations_summary"]
    executive_summary = (
        f"来源层覆盖 {counts['pdm_machines']} 台设备、{counts['pdm_telemetry']:,} 条小时级遥测、"
        f"{counts['pdm_errors']:,} 条错误事件和 {counts['pdm_failures']:,} 条故障事件。"
        f"每千观测小时故障率为 {snapshot['failure_rate_per_1000_observed_hours']:.4f}。"
        f"演示衍生层包含 {int(work_order_summary['work_order_count']):,} 张合成工单和"
        f" {counts['synthetic_daily_operations']:,} 条合成日运营记录，平均 OEE 为"
        f" {float(operations_summary['avg_oee_pct']):.2f}%。"
    )
    report_data = {
        "report_id": report_id,
        "run_id": run_id,
        "title": "Industrial PdM 数据分析与根因归因报告",
        "executive_summary": executive_summary,
        "snapshot": snapshot,
        "anomalies": anomalies or [],
        "root_cause_analysis": rca,
        "evidence_notice": (
            "pdm_* 表是未修改的来源数据层；所有 synthetic_* 表都是确定性生成的演示衍生数据，"
            "不是现场日志、已执行工单、真实成本或已确认根因。根因分数只用于候选排序。"
        ),
    }
    json_path = output_dir / "industrial_pdm_analysis_report.json"
    json_path.write_text(
        json.dumps(to_builtin(report_data), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    component_rows = [
        [row["component"], row["failure_count"]] for row in snapshot["failure_by_component"]
    ]
    machine_rows = [
        [
            row["machineID"],
            row["model_name"],
            row["model_code"],
            row["age"],
            row["failure_count"],
            row["error_count"],
        ]
        for row in snapshot["top_machines"]
    ]
    root_rows = [
        [row["root_cause"], row["event_count"]]
        for row in snapshot["synthetic_root_cause_labels"]
    ]
    work_order_rows = [
        [row["component"], row["work_order_count"], f"{float(row['downtime_hours']):,.2f}", f"{float(row['total_cost']):,.2f}"]
        for row in snapshot["synthetic_work_orders_by_component"]
    ]
    oee_rows = [
        [
            row["model_name"],
            row["model_code"],
            f"{float(row['avg_oee_pct']):.2f}%",
            f"{int(row['production_units']):,}",
            f"{int(row['scrap_units']):,}",
        ]
        for row in snapshot["synthetic_oee_by_model"]
    ]

    rca_html = "<p class='muted'>本次运行未指定单个故障事件，因此未执行事件级根因候选排序。</p>"
    if rca:
        candidate_rows = [
            [
                item["rank"],
                item["candidate_name"],
                f"{item['confidence_score']:.1f}",
                item["supporting_evidence"]["dominant_metric"],
                item["supporting_evidence"]["pre_event_error_count_72h"],
                item["supporting_evidence"]["same_component_maintenance_count_30d"],
                item["recommended_action"],
            ]
            for item in rca["candidates"]
        ]
        rca_html = (
            f"<p><strong>设备：</strong>{rca['machineID']}　"
            f"<strong>事件：</strong>{html.escape(str(rca['event_time']))}　"
            f"<strong>部件：</strong>{html.escape(str(rca['component']))}</p>"
            + _table(
                ["排名", "候选根因", "证据分", "主导指标", "前72h错误", "前30d同部件维修", "建议动作"],
                candidate_rows,
            )
            + f"<p class='warning'>{html.escape(rca['conclusion_policy'])}</p>"
        )

    anomaly_html = "<p class='muted'>未指定设备时间窗，未执行滚动稳健异常检测。</p>"
    if anomalies:
        anomaly_rows = [
            [
                item["event_time"],
                item["metric"],
                f"{item['actual_value']:.3f}",
                f"{item['robust_z']:.3f}",
                item["direction"],
                item["severity"],
            ]
            for item in anomalies[:20]
        ]
        anomaly_html = _table(
            ["时间", "指标", "实际值", "稳健Z", "方向", "等级"], anomaly_rows
        )

    css = """
    :root { color-scheme: light; --ink:#172033; --muted:#5f6b7a; --line:#dfe5ec; --blue:#1d4ed8; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f5f7fa; color:var(--ink); font-family:Arial,"Microsoft YaHei",sans-serif; line-height:1.65; }
    main { max-width:1180px; margin:28px auto; padding:0 20px 48px; }
    header { padding:34px; border-radius:18px; color:white; background:linear-gradient(135deg,#0f172a,#1d4ed8); box-shadow:0 14px 38px #1e3a8a2b; }
    h1 { margin:0 0 8px; font-size:30px; } h2 { margin-top:0; font-size:21px; }
    .subtitle { opacity:.88; margin:0; }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:18px 0; }
    .metric,.card { background:white; border:1px solid var(--line); border-radius:14px; box-shadow:0 8px 22px #0f172a0b; }
    .metric { padding:18px; } .metric span { color:var(--muted); font-size:13px; display:block; } .metric strong { font-size:25px; }
    .card { padding:24px; margin:16px 0; }
    .charts { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    .charts img { width:100%; border:1px solid var(--line); border-radius:10px; background:white; }
    .table-wrap { overflow:auto; } table { width:100%; border-collapse:collapse; font-size:14px; }
    th,td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { background:#f1f5f9; white-space:nowrap; } .muted { color:var(--muted); }
    .warning { background:#fff7ed; border-left:4px solid #f97316; padding:12px 14px; border-radius:6px; }
    .notice { background:#eff6ff; border-left:4px solid var(--blue); padding:14px; border-radius:6px; }
    @media(max-width:820px){ .grid,.charts{grid-template-columns:1fr 1fr;} }
    @media(max-width:560px){ .grid,.charts{grid-template-columns:1fr;} header{padding:24px;} }
    """
    html_text = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Industrial PdM 数据分析与根因归因报告</title><style>{css}</style></head>
<body><main>
<header><h1>Industrial PdM 数据分析与根因归因报告</h1><p class="subtitle">结构化事实 → 异常发现 → 事件钻取 → 根因候选 → 可审计报告</p></header>
<section class="grid">
  <div class="metric"><span>设备</span><strong>{counts['pdm_machines']}</strong></div>
  <div class="metric"><span>遥测点</span><strong>{counts['pdm_telemetry']:,}</strong></div>
  <div class="metric"><span>错误事件</span><strong>{counts['pdm_errors']:,}</strong></div>
  <div class="metric"><span>故障事件</span><strong>{counts['pdm_failures']:,}</strong></div>
  <div class="metric"><span>合成工单</span><strong>{counts['synthetic_work_orders']:,}</strong></div>
  <div class="metric"><span>合成日运营</span><strong>{counts['synthetic_daily_operations']:,}</strong></div>
</section>
<section class="card"><h2>执行摘要</h2><p>{html.escape(executive_summary)}</p>
<p class="notice">{html.escape(report_data['evidence_notice'])}</p></section>
<section class="card"><h2>故障趋势与结构</h2><div class="charts">
<img src="figures/{monthly_chart.name}" alt="月度故障趋势"><img src="figures/{component_chart.name}" alt="部件故障分布"></div>
{_table(['部件','故障次数'], component_rows)}</section>
<section class="card"><h2>高故障设备</h2><div class="charts"><img src="figures/{machine_chart.name}" alt="高故障设备"></div>
{_table(['设备','业务型号','来源编码','机龄','故障次数','错误次数'], machine_rows)}</section>
<section class="card"><h2>演示运营与维护成本</h2>
<p class="warning">本节的工单、成本、停机、产量、能耗和 OEE 均为由来源事件确定性生成的演示数据，不代表现场经营结果。</p>
<div class="charts"><img src="figures/{work_order_chart.name}" alt="合成工单成本"><img src="figures/{oee_chart.name}" alt="合成OEE"></div>
{_table(['部件','工单数','停机小时','总成本'], work_order_rows)}
{_table(['业务型号','来源编码','平均OEE','产量','报废量'], oee_rows)}</section>
<section class="card"><h2>稳健异常检测</h2>{anomaly_html}</section>
<section class="card"><h2>事件级根因候选</h2>{rca_html}</section>
<section class="card"><h2>合成根因标签分布</h2><p class="warning">下表来自 synthetic_fault_reports 的规则标签，仅用于演示检索和分析，不是现场确认根因。</p>
{_table(['合成根因标签','事件数'], root_rows)}</section>
<section class="card"><h2>口径与限制</h2><ul>
<li>每千观测小时故障率使用小时级遥测点近似运行小时。</li>
<li>异常点表示偏离历史基线，不自动等同于设备故障。</li>
<li>根因证据分用于排序，现场结论仍需检修、频谱、温度或工艺数据验证。</li>
<li>分析过程保存在 analysis_runs、analysis_step_logs 和 JSONL 日志中。</li>
</ul></section>
</main></body></html>"""
    html_path = output_dir / "industrial_pdm_analysis_report.html"
    html_path.write_text(html_text, encoding="utf-8")

    return {
        "report_id": report_id,
        "title": report_data["title"],
        "executive_summary": executive_summary,
        "html_path": str(html_path.resolve()),
        "json_path": str(json_path.resolve()),
        "report_data": to_builtin(report_data),
    }
