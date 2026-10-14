from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_pdm.db import Database  # noqa: E402
from industrial_pdm.provenance import TraceRun, build_dataset_version  # noqa: E402


DEFAULT_QUESTION = (
    "按设备型号比较平均OEE、来源故障次数和合成维修成本，"
    "并指出最需要优先改进的型号。"
)

# This is the SQL artifact generated from DEFAULT_QUESTION for the reproducible
# checked-in demo.  It is deliberately separate from the 20 reference cases.
# A DataAgent/LLM-produced query can be supplied at runtime with --sql-file.
DEFAULT_GENERATED_SQL = """WITH operations AS (
    SELECT
        model AS model_code,
        ROUND(AVG(oee_pct), 2) AS avg_oee_pct,
        ROUND(SUM(downtime_hours), 2) AS synthetic_downtime_hours,
        SUM(production_units) AS synthetic_production_units,
        SUM(scrap_units) AS synthetic_scrap_units,
        ROUND(
            SUM(scrap_units) * 100.0 / NULLIF(SUM(production_units), 0),
            3
        ) AS synthetic_scrap_pct
    FROM synthetic_daily_operations
    GROUP BY model
),
failures AS (
    SELECT
        m.model AS model_code,
        COUNT(f.machineID) AS source_failure_count
    FROM pdm_machines AS m
    LEFT JOIN pdm_failures AS f ON f.machineID = m.machineID
    GROUP BY m.model
),
costs AS (
    SELECT
        m.model AS model_code,
        ROUND(COALESCE(SUM(w.total_cost), 0), 2) AS synthetic_maintenance_cost
    FROM pdm_machines AS m
    LEFT JOIN synthetic_work_orders AS w ON w.machineID = m.machineID
    GROUP BY m.model
)
SELECT
    o.model_code,
    d.display_name_zh AS model_name,
    o.avg_oee_pct,
    f.source_failure_count,
    c.synthetic_maintenance_cost,
    o.synthetic_downtime_hours,
    o.synthetic_production_units,
    o.synthetic_scrap_units,
    o.synthetic_scrap_pct
FROM operations AS o
JOIN failures AS f ON f.model_code = o.model_code
JOIN costs AS c ON c.model_code = o.model_code
JOIN dim_machine_models AS d ON d.source_model_code = o.model_code
ORDER BY o.avg_oee_pct ASC, f.source_failure_count DESC"""


FAILURE_DRILLDOWN_SQL = (
    "SELECT f.failure AS component, COUNT(*) AS source_failure_count "
    "FROM pdm_failures AS f JOIN pdm_machines AS m ON m.machineID=f.machineID "
    "WHERE m.model=? GROUP BY f.failure ORDER BY source_failure_count DESC, component"
)

COST_DRILLDOWN_SQL = (
    "SELECT w.component, COUNT(*) AS synthetic_work_order_count, "
    "ROUND(SUM(w.total_cost),2) AS synthetic_total_cost "
    "FROM synthetic_work_orders AS w JOIN pdm_machines AS m ON m.machineID=w.machineID "
    "WHERE m.model=? GROUP BY w.component ORDER BY synthetic_total_cost DESC, w.component"
)

COST_BREAKDOWN_SQL = (
    "SELECT m.model AS model_code, d.display_name_zh AS model_name, "
    "ROUND(COALESCE(SUM(w.labor_cost),0),2) AS synthetic_labor_cost, "
    "ROUND(COALESCE(SUM(w.parts_cost),0),2) AS synthetic_parts_cost, "
    "ROUND(COALESCE(SUM(w.total_cost),0),2) AS synthetic_total_cost "
    "FROM pdm_machines AS m "
    "JOIN dim_machine_models AS d ON d.source_model_code=m.model "
    "LEFT JOIN synthetic_work_orders AS w ON w.machineID=m.machineID "
    "GROUP BY m.model,d.display_name_zh,d.sort_order ORDER BY d.sort_order"
)

MONTHLY_TRENDS_SQL = (
    "WITH operations AS ("
    "SELECT SUBSTR(operation_date,1,7) AS month, "
    "ROUND(AVG(oee_pct),2) AS avg_oee_pct, "
    "ROUND(SUM(downtime_hours),2) AS synthetic_downtime_hours "
    "FROM synthetic_daily_operations GROUP BY SUBSTR(operation_date,1,7)"
    "), costs AS ("
    "SELECT SUBSTR(opened_at,1,7) AS month, "
    "ROUND(SUM(total_cost),2) AS synthetic_maintenance_cost "
    "FROM synthetic_work_orders GROUP BY SUBSTR(opened_at,1,7)"
    ") SELECT o.month,o.avg_oee_pct,o.synthetic_downtime_hours,"
    "COALESCE(c.synthetic_maintenance_cost,0) AS synthetic_maintenance_cost "
    "FROM operations AS o LEFT JOIN costs AS c ON c.month=o.month ORDER BY o.month"
)

FAILURE_MATRIX_SQL = (
    "SELECT m.model AS model_code,d.display_name_zh AS model_name,"
    "f.failure AS component,COUNT(*) AS source_failure_count "
    "FROM pdm_failures AS f JOIN pdm_machines AS m ON m.machineID=f.machineID "
    "JOIN dim_machine_models AS d ON d.source_model_code=m.model "
    "GROUP BY m.model,d.display_name_zh,d.sort_order,f.failure "
    "ORDER BY d.sort_order,f.failure"
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _load_model_catalog(db: Database) -> list[dict[str, Any]]:
    return db.read_only_query(
        "SELECT source_model_code,display_name_zh,display_name_en,sort_order,is_demo_alias,note "
        "FROM dim_machine_models ORDER BY sort_order"
    )


def _enrich_model_columns(frame: pd.DataFrame, catalog: list[dict[str, Any]]) -> pd.DataFrame:
    enriched = frame.copy()
    if "model_code" not in enriched.columns and "model" in enriched.columns:
        enriched["model_code"] = enriched["model"].astype(str)
    if "model_code" not in enriched.columns:
        raise ValueError("The analysis query must return model_code (or the source model column)")
    names = {str(item["source_model_code"]): str(item["display_name_zh"]) for item in catalog}
    if "model_name" not in enriched.columns:
        enriched["model_name"] = enriched["model_code"].astype(str).map(names)
    missing = enriched.loc[enriched["model_name"].isna(), "model_code"].astype(str).unique().tolist()
    if missing:
        raise ValueError(f"Missing business display names for source model codes: {missing}")
    return enriched


def _normalise(series: pd.Series, invert: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="raise").astype(float)
    spread = float(numeric.max() - numeric.min())
    normalised = pd.Series(0.0, index=numeric.index) if spread == 0 else (numeric - numeric.min()) / spread
    return 1.0 - normalised if invert else normalised


def _score(frame: pd.DataFrame) -> pd.DataFrame:
    scored = frame.copy()
    scored["avg_oee_pct"] = pd.to_numeric(scored["avg_oee_pct"])
    scored["source_failure_count"] = pd.to_numeric(scored["source_failure_count"])
    scored["synthetic_maintenance_cost"] = pd.to_numeric(scored["synthetic_maintenance_cost"])
    scored["priority_score"] = (
        0.50 * _normalise(scored["avg_oee_pct"], invert=True)
        + 0.30 * _normalise(scored["source_failure_count"])
        + 0.20 * _normalise(scored["synthetic_maintenance_cost"])
    ) * 100.0
    scored["priority_score"] = scored["priority_score"].round(1)
    return scored.sort_values(["priority_score", "model_code"], ascending=[False, True]).reset_index(drop=True)


def _save_chart(scored: pd.DataFrame, path: Path) -> None:
    models = scored["model_name"].tolist()
    highlight = ["#d97706" if index == 0 else "#2563eb" for index in range(len(scored))]
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.6))
    metrics = [
        ("avg_oee_pct", "Average OEE by model", "OEE (%)", "{:.2f}"),
        ("source_failure_count", "Source failures by model", "Events", "{:.0f}"),
        ("synthetic_maintenance_cost", "Synthetic maintenance cost", "Cost", "{:,.0f}"),
    ]
    for axis, (column, title, ylabel, number_format) in zip(axes, metrics):
        values = pd.to_numeric(scored[column]).astype(float)
        bars = axis.bar(models, values, color=highlight)
        axis.set_title(title)
        axis.set_xlabel("Machine model")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
        axis.set_axisbelow(True)
        upper = float(values.max()) if len(values) else 1.0
        axis.set_ylim(0, upper * 1.16 if upper else 1.0)
        for bar, value in zip(bars, values):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + upper * 0.025,
                number_format.format(value),
                ha="center",
                va="bottom",
                fontsize=9,
            )
    fig.suptitle(
        "Natural-language SQL result: model comparison\n"
        "Orange = highest composite improvement priority",
        fontsize=14,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _save_bubble_chart(scored: pd.DataFrame, path: Path) -> None:
    frame = scored.copy()
    oee = pd.to_numeric(frame["avg_oee_pct"]).astype(float)
    failures = pd.to_numeric(frame["source_failure_count"]).astype(float)
    costs = pd.to_numeric(frame["synthetic_maintenance_cost"]).astype(float)
    cost_range = float(costs.max() - costs.min())
    sizes = pd.Series(700.0, index=frame.index) if cost_range == 0 else 450.0 + 1500.0 * (costs - costs.min()) / cost_range
    colors = ["#d97706" if index == 0 else "#2563eb" for index in range(len(frame))]

    fig, axis = plt.subplots(figsize=(9.2, 5.6))
    axis.scatter(oee, failures, s=sizes, c=colors, alpha=0.78, edgecolors="#172033", linewidths=0.8)
    for index, row in frame.iterrows():
        axis.annotate(
            f"{row['model_name']}\nCost {float(row['synthetic_maintenance_cost']) / 1_000_000:.2f}M",
            (float(row["avg_oee_pct"]), float(row["source_failure_count"])),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=10,
        )
    x_margin = max(float(oee.max() - oee.min()) * 0.16, 0.06)
    y_margin = max(float(failures.max() - failures.min()) * 0.16, 7.0)
    axis.set_xlim(float(oee.min()) - x_margin, float(oee.max()) + x_margin)
    axis.set_ylim(float(failures.min()) - y_margin, float(failures.max()) + y_margin)
    axis.set_title("OEE, source failures and synthetic maintenance cost")
    axis.set_xlabel("Average OEE (%) — higher is better")
    axis.set_ylabel("Source failure events — lower is better")
    axis.grid(alpha=0.25)
    axis.set_axisbelow(True)
    fig.text(
        0.5,
        0.01,
        "Bubble area = synthetic maintenance cost; orange = highest composite improvement priority",
        ha="center",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _save_cost_stack(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame([_jsonable(row) for row in rows]).sort_values("model_code")
    labor = pd.to_numeric(frame["synthetic_labor_cost"]).astype(float)
    parts = pd.to_numeric(frame["synthetic_parts_cost"]).astype(float)
    totals = labor + parts
    fig, axis = plt.subplots(figsize=(9.2, 5.2))
    axis.bar(frame["model_name"], labor, label="Labor cost", color="#2563eb")
    axis.bar(frame["model_name"], parts, bottom=labor, label="Parts cost", color="#0f766e")
    for index, total in enumerate(totals):
        axis.text(index, total + float(totals.max()) * 0.025, f"{total:,.0f}", ha="center", va="bottom", fontsize=9)
    axis.set_ylim(0, float(totals.max()) * 1.14)
    axis.set_title("Synthetic maintenance cost composition by model")
    axis.set_xlabel("Machine model")
    axis.set_ylabel("Synthetic cost")
    axis.legend(frameon=False)
    axis.grid(axis="y", alpha=0.25)
    axis.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _save_monthly_trends(rows: list[dict[str, Any]], path: Path) -> None:
    frame = pd.DataFrame([_jsonable(row) for row in rows])
    frame["month_dt"] = pd.to_datetime(frame["month"] + "-01")
    metrics = [
        ("avg_oee_pct", "Average synthetic OEE", "OEE (%)", "#2563eb"),
        ("synthetic_downtime_hours", "Synthetic downtime", "Hours", "#d97706"),
        ("synthetic_maintenance_cost", "Synthetic maintenance cost", "Cost", "#0f766e"),
    ]
    fig, axes = plt.subplots(3, 1, figsize=(10.8, 8.8), sharex=True)
    for axis, (column, title, ylabel, color) in zip(axes, metrics):
        values = pd.to_numeric(frame[column]).astype(float)
        axis.plot(frame["month_dt"], values, marker="o", linewidth=2.0, color=color)
        axis.set_title(title, loc="left")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
        axis.set_axisbelow(True)
        axis.annotate(
            f"{values.iloc[-1]:,.2f}",
            (frame["month_dt"].iloc[-1], values.iloc[-1]),
            xytext=(-4, 8),
            textcoords="offset points",
            ha="right",
            fontsize=9,
        )
    axes[-1].set_xlabel("Month")
    fig.suptitle("Monthly operating and maintenance trends")
    fig.autofmt_xdate(rotation=35)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _save_failure_heatmap(
    rows: list[dict[str, Any]], model_names: list[str], path: Path
) -> None:
    frame = pd.DataFrame([_jsonable(row) for row in rows])
    pivot = frame.pivot_table(
        index="model_name",
        columns="component",
        values="source_failure_count",
        aggfunc="sum",
        fill_value=0,
    )
    ordered_models = [model for model in model_names if model in pivot.index]
    pivot = pivot.reindex(index=ordered_models, columns=sorted(pivot.columns))
    matrix = pivot.to_numpy(dtype=float)
    fig, axis = plt.subplots(figsize=(8.8, 4.8))
    image = axis.imshow(matrix, cmap="Blues", aspect="auto")
    axis.set_xticks(range(len(pivot.columns)), pivot.columns)
    axis.set_yticks(range(len(pivot.index)), pivot.index)
    axis.set_xlabel("Failure component")
    axis.set_ylabel("Machine model")
    axis.set_title("Source failure count: model × component")
    threshold = float(matrix.max()) * 0.55 if matrix.size else 0
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = int(matrix[row_index, column_index])
            axis.text(
                column_index,
                row_index,
                str(value),
                ha="center",
                va="center",
                color="white" if value >= threshold else "#172033",
                fontsize=10,
            )
    colorbar = fig.colorbar(image, ax=axis, pad=0.02)
    colorbar.set_label("Source failure events")
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _html_table(frame: pd.DataFrame) -> str:
    headers = "".join(f"<th>{html.escape(str(column))}</th>" for column in frame.columns)
    rows = []
    for _, row in frame.iterrows():
        cells = "".join(f"<td>{html.escape(str(value))}</td>" for value in row.tolist())
        rows.append(f"<tr>{cells}</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"


def _recommendations(
    scored: pd.DataFrame,
    failure_drilldown: list[dict[str, Any]],
    cost_drilldown: list[dict[str, Any]],
) -> list[str]:
    target = scored.iloc[0]
    best_oee = float(scored["avg_oee_pct"].max())
    target_label = f"{target['model_name']}（来源编码 {target['model_code']}）"
    recommendations = [
        (
            f"把 {target_label} 作为首轮改善对象：其平均 OEE 为 {float(target['avg_oee_pct']):.2f}%，"
            f"比最佳型号低 {best_oee - float(target['avg_oee_pct']):.2f} 个百分点；来源故障"
            f" {int(target['source_failure_count'])} 次，合成维修成本 {float(target['synthetic_maintenance_cost']):,.2f}。"
        )
    ]
    if failure_drilldown:
        top = failure_drilldown[0]
        recommendations.append(
            f"先核查 {target['model_name']} 的 {top['component']}：该部件来源故障最多，共 {int(top['source_failure_count'])} 次。"
        )
    if cost_drilldown:
        top = cost_drilldown[0]
        recommendations.append(
            f"同步复核 {top['component']} 工单策略：该部件合成工单成本最高，为 {float(top['synthetic_total_cost']):,.2f}；"
            "请用真实 CMMS 工单和采购价格验证后再作预算决策。"
        )
    recommendations.append(
        "把 OEE、故障和成本按型号继续下钻到设备与部件，并用现场停机原因、检修记录和质量数据验证；"
        "本报告中的 OEE、工单与成本属于合成演示层。"
    )
    return recommendations


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute and render one NL-to-SQL demo")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--sql-file", type=Path)
    parser.add_argument(
        "--db-url",
        default=os.getenv(
            "INDUSTRIAL_PDM_DB_URL",
            f"sqlite:///{(ROOT / 'generated' / 'runtime' / 'industrial_pdm.sqlite').as_posix()}",
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "generated" / "reports" / "nl_sql_demo",
    )
    parser.add_argument(
        "--generator-label",
        default="Codex natural-language-to-SQL demonstration",
    )
    args = parser.parse_args()

    sql = args.sql_file.read_text(encoding="utf-8") if args.sql_file else DEFAULT_GENERATED_SQL
    db = Database(args.db_url)
    model_catalog = _load_model_catalog(db)
    source_manifest = json.loads((ROOT / "synthetic" / "manifest.json").read_text(encoding="utf-8"))
    model_catalog_document = json.loads(
        (ROOT / "config" / "model_catalog.json").read_text(encoding="utf-8")
    )
    dataset_version = build_dataset_version(source_manifest, model_catalog_document)
    trace = TraceRun(args.question, "industrial_pdm", dataset_version)

    question_artifact = trace.add_inline_artifact(
        "question",
        "用户自然语言问题",
        {"text": args.question},
        evidence_class="user_input",
    )
    dataset_artifact = trace.add_inline_artifact(
        "dataset_snapshot",
        "数据与语义目录快照",
        {
            "source_sha256": source_manifest["source_sha256"],
            "generator_version": source_manifest["generator_version"],
            "model_catalog": model_catalog,
        },
        evidence_class="source_and_synthetic_manifest",
        metadata={"dataset_version": dataset_version},
    )
    plan_artifact = trace.add_inline_artifact(
        "agent_plan",
        "任务与呈现计划",
        {
            "primary_goal": "比较设备型号并确定优先改善对象",
            "dimensions": ["machine_model"],
            "metrics": ["average_oee", "source_failure_count", "synthetic_maintenance_cost"],
            "presentation": ["result_table", "comparison_charts", "evidence_backed_analysis", "recommendations"],
            "policy": "用最少但完整的呈现满足比较、解释与建议需求",
        },
        parent_ids=[question_artifact, dataset_artifact],
        evidence_class="agent_decision_summary",
    )
    trace.add_step(
        "understand_and_plan",
        "DataAgent planner",
        input_artifact_ids=[question_artifact, dataset_artifact],
        output_artifact_ids=[plan_artifact],
        summary="识别为多指标型号比较，需要结果、图表、分析和建议。",
    )
    sql_artifact = trace.add_inline_artifact(
        "sql",
        "生成的主查询 SQL",
        {"dialect": db.dialect, "sql": sql, "parameters": []},
        parent_ids=[plan_artifact, dataset_artifact],
        evidence_class="generated_query",
        metadata={"generator": args.generator_label},
    )
    trace.add_step(
        "generate_and_validate_sql",
        args.generator_label,
        input_artifact_ids=[plan_artifact, dataset_artifact],
        output_artifact_ids=[sql_artifact],
        summary="生成并通过只读 SQL 安全校验。",
    )

    pipeline_started = time.perf_counter()
    started = time.perf_counter()
    rows = db.read_only_query(sql)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    if not rows:
        raise RuntimeError("Generated SQL returned no rows")

    result_frame = _enrich_model_columns(
        pd.DataFrame([_jsonable(row) for row in rows]), model_catalog
    )
    main_result_artifact = trace.add_inline_artifact(
        "query_result",
        "主查询结果",
        result_frame.to_dict(orient="records"),
        parent_ids=[sql_artifact, dataset_artifact],
        evidence_class="mixed_source_and_synthetic",
        metadata={
            "row_count": len(result_frame),
            "columns": result_frame.columns.tolist(),
            "elapsed_ms": elapsed_ms,
        },
    )
    trace.add_step(
        "execute_query",
        "query_database",
        input_artifact_ids=[sql_artifact, dataset_artifact],
        output_artifact_ids=[main_result_artifact],
        duration_ms=elapsed_ms,
        summary=f"返回 {len(result_frame)} 行。",
    )

    scored = _score(result_frame)
    target_model_code = str(scored.iloc[0]["model_code"])
    target_model_name = str(scored.iloc[0]["model_name"])
    priority_method = (
        "演示优先级分 = 50%×低OEE归一化 + 30%×来源故障归一化 + "
        "20%×合成维修成本归一化；仅用于演示排序。"
    )
    score_artifact = trace.add_inline_artifact(
        "derived_metric",
        "型号改善优先级排序",
        {"method": priority_method, "ranking": scored.to_dict(orient="records")},
        parent_ids=[main_result_artifact],
        evidence_class="derived_demo_score",
    )
    trace.add_step(
        "profile_and_rank",
        "deterministic scoring",
        input_artifact_ids=[main_result_artifact],
        output_artifact_ids=[score_artifact],
        summary="检查结果形状并按公开公式生成改善优先级。",
    )

    failure_drilldown = db.read_only_query(FAILURE_DRILLDOWN_SQL, (target_model_code,))
    cost_drilldown = db.read_only_query(COST_DRILLDOWN_SQL, (target_model_code,))
    cost_breakdown = db.read_only_query(COST_BREAKDOWN_SQL)
    monthly_trends = db.read_only_query(MONTHLY_TRENDS_SQL)
    failure_matrix = db.read_only_query(FAILURE_MATRIX_SQL)
    failure_artifact = trace.add_inline_artifact(
        "drilldown_result",
        "优先型号来源故障下钻",
        {"sql": FAILURE_DRILLDOWN_SQL, "parameters": [target_model_code], "rows": failure_drilldown},
        parent_ids=[score_artifact, dataset_artifact],
        evidence_class="source",
    )
    cost_artifact = trace.add_inline_artifact(
        "drilldown_result",
        "优先型号合成工单成本下钻",
        {"sql": COST_DRILLDOWN_SQL, "parameters": [target_model_code], "rows": cost_drilldown},
        parent_ids=[score_artifact, dataset_artifact],
        evidence_class="synthetic_demo",
    )
    cost_breakdown_artifact = trace.add_inline_artifact(
        "query_result",
        "型号合成成本构成",
        {"sql": COST_BREAKDOWN_SQL, "parameters": [], "rows": cost_breakdown},
        parent_ids=[dataset_artifact],
        evidence_class="synthetic_demo",
    )
    monthly_artifact = trace.add_inline_artifact(
        "query_result",
        "月度运营与维修趋势",
        {"sql": MONTHLY_TRENDS_SQL, "parameters": [], "rows": monthly_trends},
        parent_ids=[dataset_artifact],
        evidence_class="synthetic_demo",
    )
    failure_matrix_artifact = trace.add_inline_artifact(
        "query_result",
        "型号与部件来源故障矩阵",
        {"sql": FAILURE_MATRIX_SQL, "parameters": [], "rows": failure_matrix},
        parent_ids=[dataset_artifact],
        evidence_class="source",
    )
    trace.add_step(
        "replan_and_drill_down",
        "query_database",
        input_artifact_ids=[score_artifact, dataset_artifact],
        output_artifact_ids=[
            failure_artifact,
            cost_artifact,
            cost_breakdown_artifact,
            monthly_artifact,
            failure_matrix_artifact,
        ],
        summary="根据主结果选择优先型号，并补充故障、成本构成、趋势和部件分布证据。",
    )
    recommendations = _recommendations(scored, failure_drilldown, cost_drilldown)
    pipeline_elapsed_ms = round((time.perf_counter() - pipeline_started) * 1000.0, 2)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = output_dir / "model_priority_comparison.png"
    bubble_path = output_dir / "oee_failure_cost_bubble.png"
    cost_stack_path = output_dir / "maintenance_cost_composition.png"
    monthly_path = output_dir / "monthly_operating_trends.png"
    heatmap_path = output_dir / "model_component_failure_heatmap.png"
    csv_path = output_dir / "query_result.csv"
    json_path = output_dir / "nl_sql_demo_report.json"
    html_path = output_dir / "nl_sql_demo_report.html"
    sql_path = output_dir / "generated_query.sql"
    trace_path = output_dir / "provenance_manifest.json"

    _save_chart(scored, chart_path)
    _save_bubble_chart(scored, bubble_path)
    _save_cost_stack(cost_breakdown, cost_stack_path)
    _save_monthly_trends(monthly_trends, monthly_path)
    _save_failure_heatmap(failure_matrix, scored["model_name"].tolist(), heatmap_path)
    result_frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    sql_path.write_text(sql.strip() + "\n", encoding="utf-8")

    sql_file_artifact = trace.add_file_artifact(
        "sql_file", "主查询 SQL 文件", sql_path, parent_ids=[sql_artifact]
    )
    csv_artifact = trace.add_file_artifact(
        "result_file", "主查询 CSV 结果", csv_path, parent_ids=[main_result_artifact]
    )
    chart_artifact = trace.add_file_artifact(
        "chart",
        "型号基础指标小多图",
        chart_path,
        parent_ids=[score_artifact],
        metadata={"chart_type": "small_multiples_bar", "labels": "model_name"},
    )
    bubble_artifact = trace.add_file_artifact(
        "chart",
        "OEE、故障和成本气泡图",
        bubble_path,
        parent_ids=[score_artifact],
        metadata={
            "chart_type": "bubble_scatter",
            "x": "avg_oee_pct",
            "y": "source_failure_count",
            "size": "synthetic_maintenance_cost",
        },
    )
    cost_stack_artifact = trace.add_file_artifact(
        "chart",
        "合成维修成本构成图",
        cost_stack_path,
        parent_ids=[cost_breakdown_artifact],
        metadata={"chart_type": "stacked_bar"},
    )
    monthly_chart_artifact = trace.add_file_artifact(
        "chart",
        "月度运营与维修趋势图",
        monthly_path,
        parent_ids=[monthly_artifact],
        metadata={"chart_type": "multi_panel_line"},
    )
    heatmap_artifact = trace.add_file_artifact(
        "chart",
        "型号与部件来源故障热力图",
        heatmap_path,
        parent_ids=[failure_matrix_artifact],
        evidence_class="source",
        metadata={"chart_type": "heatmap"},
    )
    trace.add_step(
        "render_visualizations",
        "render_chart",
        input_artifact_ids=[
            score_artifact,
            cost_breakdown_artifact,
            monthly_artifact,
            failure_matrix_artifact,
        ],
        output_artifact_ids=[
            chart_artifact,
            bubble_artifact,
            cost_stack_artifact,
            monthly_chart_artifact,
            heatmap_artifact,
        ],
        summary="根据结果形状生成比较、关系、构成、趋势和矩阵图。",
    )

    analysis_artifact = trace.add_inline_artifact(
        "analysis",
        "图表与数据联合分析",
        {"recommendations": recommendations, "priority_model_code": target_model_code},
        parent_ids=[
            score_artifact,
            failure_artifact,
            cost_artifact,
            bubble_artifact,
            heatmap_artifact,
        ],
        evidence_class="evidence_backed_analysis",
    )
    claim_ids: list[str] = []
    claim_ids.append(
        trace.add_claim(
            recommendations[0],
            [score_artifact, main_result_artifact, bubble_artifact],
            limitations="综合优先级权重是演示规则，不是现场批准的决策模型。",
        )
    )
    recommendation_index = 1
    if failure_drilldown:
        claim_ids.append(
            trace.add_claim(recommendations[recommendation_index], [failure_artifact, heatmap_artifact])
        )
        recommendation_index += 1
    if cost_drilldown:
        claim_ids.append(
            trace.add_claim(
                recommendations[recommendation_index],
                [cost_artifact, cost_stack_artifact],
                limitations="工单和成本为合成演示数据，需由真实 CMMS 与采购数据复核。",
            )
        )
        recommendation_index += 1
    claim_ids.append(
        trace.add_claim(
            recommendations[recommendation_index],
            [dataset_artifact, analysis_artifact],
            claim_type="qualified_recommendation",
            limitations="该建议要求补充现场证据后再决策。",
        )
    )
    trace.add_step(
        "analyze_and_verify_claims",
        "analyze_visualized_result + verify_claims",
        input_artifact_ids=[
            score_artifact,
            failure_artifact,
            cost_artifact,
            bubble_artifact,
            heatmap_artifact,
        ],
        output_artifact_ids=[analysis_artifact],
        summary="分析同时读取底层结果与图表规格；所有发布结论均绑定已解析证据。",
    )

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    visualization_records = [
        {"type": "small_multiples_bar", "path": chart_path.name, "artifact_id": chart_artifact},
        {"type": "bubble_scatter", "path": bubble_path.name, "artifact_id": bubble_artifact},
        {"type": "stacked_bar", "path": cost_stack_path.name, "artifact_id": cost_stack_artifact},
        {"type": "multi_panel_line", "path": monthly_path.name, "artifact_id": monthly_chart_artifact},
        {"type": "heatmap", "path": heatmap_path.name, "artifact_id": heatmap_artifact},
    ]
    payload = {
        "run_id": trace.run_id,
        "dataset_version": dataset_version,
        "generated_at": generated_at,
        "question": args.question,
        "sql_generator": args.generator_label,
        "sql_dialect": db.dialect,
        "generated_sql": sql,
        "query_elapsed_ms": elapsed_ms,
        "pipeline_elapsed_ms": pipeline_elapsed_ms,
        "query_result": result_frame.to_dict(orient="records"),
        "model_catalog": model_catalog,
        "priority_method": priority_method,
        "priority_ranking": scored.to_dict(orient="records"),
        "priority_model": {"model_code": target_model_code, "model_name": target_model_name},
        "failure_drilldown": failure_drilldown,
        "synthetic_cost_drilldown": cost_drilldown,
        "synthetic_cost_breakdown": cost_breakdown,
        "monthly_trends": monthly_trends,
        "source_failure_matrix": failure_matrix,
        "visualizations": visualization_records,
        "recommendations": [
            {"text": text, "claim_id": claim_id}
            for text, claim_id in zip(recommendations, claim_ids)
        ],
        "provenance_manifest": trace_path.name,
        "evidence_notice": (
            "pdm_failures 是来源层；OEE、停机、工单和成本来自 synthetic_* 确定性演示层，"
            "不得当作现场经营事实。A/B/C/D 型机组是中性演示别名，原始编码始终保留。"
        ),
    }
    json_path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    display_result = result_frame.copy()
    for column in ["avg_oee_pct", "synthetic_maintenance_cost", "synthetic_downtime_hours", "synthetic_scrap_pct"]:
        if column in display_result:
            display_result[column] = pd.to_numeric(display_result[column]).round(2)
    display_result = display_result.rename(
        columns={
            "model_name": "业务型号",
            "model_code": "来源编码",
            "avg_oee_pct": "平均OEE(%)",
            "source_failure_count": "来源故障次数",
            "synthetic_maintenance_cost": "合成维修成本",
            "synthetic_downtime_hours": "合成停机小时",
            "synthetic_production_units": "合成产量",
            "synthetic_scrap_units": "合成报废量",
            "synthetic_scrap_pct": "合成报废率(%)",
        }
    )
    preferred_columns = [
        "业务型号",
        "来源编码",
        "平均OEE(%)",
        "来源故障次数",
        "合成维修成本",
        "合成停机小时",
        "合成产量",
        "合成报废量",
        "合成报废率(%)",
    ]
    display_result = display_result[
        [column for column in preferred_columns if column in display_result.columns]
        + [column for column in display_result.columns if column not in preferred_columns]
    ]
    display_score = scored[
        ["model_name", "model_code", "priority_score", "avg_oee_pct", "source_failure_count", "synthetic_maintenance_cost"]
    ].copy()
    display_score.columns = ["业务型号", "来源编码", "优先级分", "平均OEE(%)", "来源故障次数", "合成维修成本"]
    recommendations_html = "".join(
        f"<li>{html.escape(text)} <a href='#{claim_id}'>[{claim_id}]</a></li>"
        for text, claim_id in zip(recommendations, claim_ids)
    )
    model_rows = [
        [item["display_name_zh"], item["source_model_code"], "演示别名", item["note"]]
        for item in model_catalog
    ]
    model_display = pd.DataFrame(
        model_rows, columns=["业务显示名称", "来源编码", "名称性质", "说明"]
    )
    claim_rows = "".join(
        "<tr id='{claim_id}'><td>{claim_id}</td><td>{statement}</td><td>{refs}</td><td>{limits}</td></tr>".format(
            claim_id=html.escape(claim["claim_id"]),
            statement=html.escape(claim["statement"]),
            refs=" ".join(
                f"<a href='#{html.escape(ref)}'>{html.escape(ref)}</a>"
                for ref in claim["evidence_artifact_ids"]
            ),
            limits=html.escape(claim.get("limitations") or "无附加限制"),
        )
        for claim in trace.claims
    )
    artifact_rows = "".join(
        "<tr id='{artifact_id}'><td>{artifact_id}</td><td>{kind}</td><td>{label}</td>"
        "<td><code>{sha}</code></td><td>{parents}</td></tr>".format(
            artifact_id=html.escape(artifact["artifact_id"]),
            kind=html.escape(artifact["kind"]),
            label=html.escape(artifact["label"]),
            sha=html.escape(artifact["sha256"][:16]),
            parents=" ".join(
                f"<a href='#{html.escape(parent)}'>{html.escape(parent)}</a>"
                for parent in artifact["parent_ids"]
            )
            or "—",
        )
        for artifact in trace.artifacts
    )
    css = """
    :root{color-scheme:light;--ink:#172033;--muted:#5f6b7a;--line:#dfe5ec;--blue:#1d4ed8;--amber:#d97706}
    *{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:var(--ink);font-family:Arial,"Microsoft YaHei",sans-serif;line-height:1.65}
    main{max-width:1180px;margin:28px auto;padding:0 20px 48px}header{padding:30px;border-radius:16px;color:white;background:linear-gradient(135deg,#0f172a,#1d4ed8)}
    h1{margin:0 0 8px;font-size:28px}h2{font-size:20px;margin-top:0}.subtitle{margin:0;opacity:.9}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:22px;margin:16px 0}
    .meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{background:#f8fafc;padding:14px;border-radius:10px}.metric span{display:block;color:var(--muted);font-size:13px}.metric strong{font-size:20px}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0f172a;color:#e2e8f0;padding:18px;border-radius:10px;overflow:auto}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}
    th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{background:#f1f5f9}img{width:100%;height:auto;border:1px solid var(--line);border-radius:10px}
    .visual-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}figure{margin:16px 0}figcaption{color:var(--muted);font-size:13px;margin-top:7px}
    .notice{background:#eff6ff;border-left:4px solid var(--blue);padding:12px 14px;border-radius:6px}.warning{background:#fff7ed;border-left:4px solid var(--amber);padding:12px 14px;border-radius:6px}
    a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}code{overflow-wrap:anywhere}details{margin-top:14px}summary{cursor:pointer;font-weight:600}
    @media(max-width:820px){.meta,.visual-grid{grid-template-columns:1fr 1fr}}@media(max-width:620px){.meta,.visual-grid{grid-template-columns:1fr}}
    """
    html_text = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>自然语言到 SQL 完整演示报告</title><style>{css}</style></head><body><main>
    <header><h1>自然语言 → SQL → 查询 → 可视化 → 分析建议</h1><p class="subtitle">Industrial PdM 单问题端到端演示</p></header>
    <section class="card"><h2>自然语言问题</h2><p>{html.escape(args.question)}</p><div class="meta">
    <div class="metric"><span>SQL 方言</span><strong>{html.escape(db.dialect)}</strong></div><div class="metric"><span>返回行数</span><strong>{len(result_frame)}</strong></div>
    <div class="metric"><span>主查询耗时</span><strong>{elapsed_ms:.2f} ms</strong></div>
    <div class="metric"><span>分析查询总耗时</span><strong>{pipeline_elapsed_ms:.2f} ms</strong></div></div></section>
    <section class="card"><h2>型号名称说明</h2><p class="notice">原公开样例只给出 model1–model4，没有真实厂商型号名。报告使用中性演示别名提升可读性，同时始终保留来源编码。</p>
    {_html_table(model_display)}</section>
    <section class="card"><h2>生成的 SQL</h2><p class="notice">生成者：{html.escape(args.generator_label)}。本问题不属于 20 条参考问题的原句。证据：<a href="#{sql_artifact}">{sql_artifact}</a></p><pre><code>{html.escape(sql)}</code></pre></section>
    <section class="card"><h2>查询结果</h2>{_html_table(display_result)}<p>证据：<a href="#{main_result_artifact}">{main_result_artifact}</a>；下载结果：<a href="{csv_path.name}">{csv_path.name}</a></p></section>
    <section class="card"><h2>多维结果可视化</h2>
    <div class="visual-grid">
    <figure><img src="{bubble_path.name}" alt="平均OEE、来源故障次数和合成维修成本气泡图"><figcaption>气泡图：横轴为平均 OEE，纵轴为来源故障次数，面积表示合成维修成本；橙色为综合优先级最高型号。<a href="#{bubble_artifact}">查看证据</a></figcaption></figure>
    <figure><img src="{cost_stack_path.name}" alt="各型号合成人工成本和备件成本堆叠图"><figcaption>成本构成：区分合成人工成本与合成备件成本，避免只看总额。<a href="#{cost_stack_artifact}">查看证据</a></figcaption></figure>
    </div>
    <figure><img src="{monthly_path.name}" alt="月度平均OEE、停机小时和维修成本趋势折线图"><figcaption>月度趋势：同时观察平均 OEE、合成停机小时和合成维修成本的时间变化。<a href="#{monthly_chart_artifact}">查看证据</a></figcaption></figure>
    <div class="visual-grid">
    <figure><img src="{heatmap_path.name}" alt="设备型号与部件来源故障次数热力图"><figcaption>故障热力图：来源故障按型号和部件交叉分布，用于确定下钻方向。<a href="#{heatmap_artifact}">查看证据</a></figcaption></figure>
    <figure><img src="{chart_path.name}" alt="各型号基础指标柱状对比"><figcaption>基础指标小多图：保持 OEE、故障数与成本的原始量纲和直接数值。<a href="#{chart_artifact}">查看证据</a></figcaption></figure>
    </div></section>
    <section class="card"><h2>分析结论</h2><p>{html.escape(payload['priority_method'])}</p>{_html_table(display_score)}
    <p class="warning">优先改善型号：<strong>{html.escape(target_model_name)}</strong>（来源编码 {html.escape(target_model_code)}）。该排序是演示性决策辅助，不是现场诊断结论。<a href="#{claim_ids[0]}">[{claim_ids[0]}]</a></p></section>
    <section class="card"><h2>建议</h2><ol>{recommendations_html}</ol><p class="notice">{html.escape(payload['evidence_notice'])}</p></section>
    <section class="card"><h2>结论证据索引</h2><div class="table-wrap"><table><thead><tr><th>结论编号</th><th>显示结论</th><th>证据产物</th><th>限制</th></tr></thead><tbody>{claim_rows}</tbody></table></div></section>
    <section class="card"><h2>完整产物链路</h2><p>每个产物都包含内容哈希和上游产物，完整机器可读记录见 <a href="{trace_path.name}">{trace_path.name}</a>。</p>
    <details><summary>展开产物与父级关系</summary><div class="table-wrap"><table><thead><tr><th>产物ID</th><th>类型</th><th>名称</th><th>SHA-256 前缀</th><th>上游产物</th></tr></thead><tbody>{artifact_rows}</tbody></table></div></details></section>
    <section class="card"><h2>运行信息</h2><p>运行编号：<code>{trace.run_id}</code><br>数据版本：<code>{dataset_version}</code><br>生成时间：{html.escape(generated_at)}<br>图表：5 张（柱状小多图、气泡散点、成本堆叠、月度折线、故障热力图）<br>JSON：<a href="{json_path.name}">{json_path.name}</a><br>CSV：<a href="{csv_path.name}">{csv_path.name}</a><br>SQL：<a href="{sql_path.name}">{sql_path.name}</a><br>完整溯源：<a href="{trace_path.name}">{trace_path.name}</a></p></section>
    </main></body></html>"""
    html_path.write_text(html_text, encoding="utf-8")

    json_artifact = trace.add_file_artifact(
        "report_data",
        "结构化分析报告",
        json_path,
        parent_ids=[analysis_artifact, csv_artifact, *[item["artifact_id"] for item in visualization_records]],
    )
    html_artifact = trace.add_file_artifact(
        "report",
        "可视化分析报告",
        html_path,
        parent_ids=[json_artifact, sql_file_artifact],
    )
    trace.add_step(
        "compose_report",
        "compose_report",
        input_artifact_ids=[analysis_artifact, csv_artifact, sql_file_artifact],
        output_artifact_ids=[json_artifact, html_artifact],
        summary="仅使用已绑定证据的结论生成报告，并保留来源与合成边界。",
    )
    trace.finish("succeeded")
    trace.write_manifest(trace_path)

    print(f"QUESTION={args.question}")
    print(f"DIALECT={db.dialect}")
    print(f"ROWS={len(result_frame)} ELAPSED_MS={elapsed_ms}")
    print(
        f"PRIORITY_MODEL={target_model_name} SOURCE_CODE={target_model_code} "
        f"SCORE={float(scored.iloc[0]['priority_score']):.1f}"
    )
    print(f"RUN_ID={trace.run_id}")
    print(f"DATASET_VERSION={dataset_version}")
    print(f"HTML={html_path}")
    print(f"JSON={json_path}")
    print(f"TRACE={trace_path}")
    print("CHARTS=5")
    for path in [chart_path, bubble_path, cost_stack_path, monthly_path, heatmap_path]:
        print(f"CHART={path}")


if __name__ == "__main__":
    main()
