"""Closed report claims: the model selects coordinates, never supplies facts or prose.

The renderer is the trust boundary. Each published assertion is compiled from
immutable query rows or a locally recomputed calculation, with a bounded scope.
"""

from __future__ import annotations

import html
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from dataagent.artifacts import ArtifactStore, canonical_json, fingerprint
from dataagent.calculations import AggregateSpec, CalculationSpec, calculate


class StrictClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RowClaim(StrictClaim):
    type: Literal["row"]
    source_id: str
    row: int = Field(ge=0)


class CellClaim(StrictClaim):
    type: Literal["cell"]
    source_id: str
    row: int = Field(ge=0)
    field: str


class ExtremeClaim(StrictClaim):
    type: Literal["extremum"]
    source_id: str
    measure: str
    labels: list[str] = Field(default_factory=list, max_length=4)
    operation: Literal["min", "max"]


class MethodClaim(StrictClaim):
    type: Literal["method"]
    source_id: str


Claim = Annotated[RowClaim | CellClaim | ExtremeClaim | MethodClaim, Field(discriminator="type")]


class Followup(StrictClaim):
    action: Literal[
        "verify_scope",
        "collect_operating_hours",
        "collect_repair_endpoints",
        "compare_matched_controls",
        "check_data_quality",
        "engineering_review",
    ]
    source_id: str


class ReportClaimsPlan(StrictClaim):
    # No model-supplied numeric values, assertions, headings or method names.
    claims: list[Claim] = Field(default_factory=list, max_length=80)
    followups: list[Followup] = Field(default_factory=list, max_length=12)


FOLLOWUPS = {
    "verify_scope": "核对查询筛选范围和指标口径后，再判断结果是否适用于业务总体。",
    "collect_operating_hours": "补齐实际运行时长、负载和设备工况，作为暴露量及混杂控制信息。",
    "collect_repair_endpoints": "补齐维修开始和结束时间，再评估修复时长；缺少结束时间不能计算真实 MTTR。",
    "compare_matched_controls": (
        "建立同期、同工况对照，预先定义效应指标和反证检验；统计关联暂不作为因果结论。"
    ),
    "check_data_quality": "检查源数据的空值、重复键、时间覆盖和缺失机制；聚合结果不等于原始数据质量合格。",
    "engineering_review": "由现场人员核对故障记录、检修证据和干预效果，审核候选原因。",
}


def display(value: Any) -> str:
    """Keep source strings inert inside a generated Markdown table."""
    if value is None:
        return "NULL（不可用，不等于零）"
    text = str(value) if not isinstance(value, (dict, list)) else canonical_json(value)
    special = {"\\": "\\\\", "|": "\\|", "`": "&#96;", "\n": "<br>", "\r": ""}
    special.update({character: f"&#{ord(character)};" for character in "[]()!*_~#"})
    return "".join(special.get(character, html.escape(character)) for character in text)


def _table(row: dict[str, Any], fields: list[str], labels: dict[str, str]) -> str:
    return (
        "| "
        + " | ".join(display(labels.get(f, f)) for f in fields)
        + " |\n"
        + "| "
        + " | ".join("---" for _ in fields)
        + " |\n"
        + "| "
        + " | ".join(display(row[f]) for f in fields)
        + " |"
    )


def _execution_scope_lines(payload: dict[str, Any]) -> list[str]:
    """Display saved execution filters, never reconstruct them from a request."""
    source = payload.get("source")
    source = source if isinstance(source, dict) else {}
    registry = source.get("metric_registry")
    registry = registry if isinstance(registry, dict) else {}
    execution = registry.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    dataset_id = source.get("dataset_id", registry.get("dataset_id"))
    candidates = [execution.get("scope"), source.get("scope")]
    if dataset_id == "scania_component_x":
        candidates.append(source.get("filters"))
    scope = next((candidate for candidate in candidates if isinstance(candidate, dict)), None)
    lines = [f"  - 实际执行数据集：{display(dataset_id)}。"] if dataset_id is not None else []
    if scope is None:
        return lines + [
            "  - 未保存结构化执行范围，请查看该证据的执行 SQL 与参数；不从提问推断日期、设备或业务总体范围。"
        ]
    labels = {
        "startDate": "开始日期",
        "start_date": "开始日期",
        "endDate": "结束日期",
        "end_date": "结束日期",
        "machineId": "设备编号",
        "machine_id": "设备编号",
        "model": "设备型号",
        "failureType": "故障类型",
        "failure_type": "故障类型",
        "split": "数据分区",
        "vehicle_id": "车辆编号",
        "min_time": "相对时间下限（非日历日期）",
        "max_time": "相对时间上限（非日历日期）",
        "specifications": "规格筛选",
        "class_label": "类别标签",
        "in_study_repair": "研究期间维修标记",
    }
    filters = "；".join(
        f"{display(labels.get(key, key))}：{display('NULL（未设置）' if value is None else value)}"
        for key, value in scope.items()
    )
    lines.append(f"  - 实际执行筛选：{filters or display(scope)}。")
    return lines


def _result(store: ArtifactStore, run_id: str, agent_id: int, aid: str) -> dict[str, Any]:
    artifact = store.get(run_id, agent_id, aid)
    if artifact["kind"] not in {"query_result", "result_calculation"}:
        raise ValueError("Claims require query_result or a verified result_calculation")
    payload = artifact["content"]
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("column"), list)
        or not isinstance(payload.get("data"), list)
    ):
        raise ValueError("Claim source is not a structured result")
    if any(not isinstance(field, str) or not field for field in payload["column"]):
        raise ValueError("Result columns must be nonempty field identifiers")
    if len(set(payload["column"])) != len(payload["column"]):
        raise ValueError("Duplicate result columns")
    if any(not isinstance(row, dict) or set(payload["column"]) != set(row) for row in payload["data"]):
        raise ValueError("Result row does not match its schema")
    if payload.get("errorMsg") or payload.get("rowCount") != len(payload["data"]):
        raise ValueError("Result is failed or has inconsistent rowCount")
    if artifact["kind"] == "query_result":
        parents = [store.get(run_id, agent_id, aid) for aid in artifact["parentIds"]]
        if not any(parent["kind"] == "sql" for parent in parents):
            raise ValueError("Result has no executed SQL provenance")
        manifest = store.load(run_id, agent_id)
        if not any(
            step["status"] == "succeeded"
            and step["node"] in {"execute_query", "SQL_EXECUTE_NODE", "BI_EXECUTE_NODE"}
            and artifact["artifactId"] in step["outputArtifactIds"]
            for step in manifest["steps"]
        ):
            raise ValueError("Result has no successful execution record")
    if artifact["kind"] == "result_calculation":
        spec = TypeAdapter(CalculationSpec).validate_python(payload["spec"])
        if spec.result_artifact_id not in artifact["parentIds"]:
            raise ValueError("Calculation source is not a bound parent")
        source = store.get(run_id, agent_id, spec.result_artifact_id)
        if source["kind"] != "query_result" or calculate(source["content"], spec) != payload:
            raise ValueError("Calculation cannot be independently reproduced")
        _result(store, run_id, agent_id, source["artifactId"])
    return artifact


def _controlled_labels(source: dict[str, Any]) -> dict[str, str]:
    """Calculation aliases are model IDs, never trusted presentation labels."""
    payload = source["content"]
    if source["kind"] != "result_calculation":
        return {
            metric["metric_id"]: f"{metric['name']} ({metric['unit']})"
            for metric in payload.get("metric_definitions", [])
        }
    spec = TypeAdapter(CalculationSpec).validate_python(payload["spec"])
    if isinstance(spec, AggregateSpec):
        return {metric.name: f"{metric.operation}({metric.field})" for metric in spec.metrics}
    return {
        "measure": "原始指标字段",
        "baseline": "基准期值",
        "current": "当前期值",
        "difference": "当前值减基准值",
        "change_pct": "相对变化百分数",
        "status": "计算状态",
    }


def compile_claim(store: ArtifactStore, run_id: str, agent_id: int, claim: Claim) -> dict[str, Any]:
    source = _result(store, run_id, agent_id, claim.source_id)
    payload = source["content"]
    columns, rows = payload["column"], payload["data"]
    labels = _controlled_labels(source)
    scope = (
        "本次返回行（结果已截断，不代表全量）"
        if payload.get("truncated")
        else "本次查询返回行（不自动代表业务总体）"
    )
    bindings: list[dict[str, Any]] = []
    if isinstance(claim, MethodClaim):
        if source["kind"] != "result_calculation":
            raise ValueError(
                "Method assertions require a reproduced controlled calculation, not imports or requested code"
            )
        # Omit model-created output aliases from the published method declaration.
        method = dict(payload["spec"])
        if "metrics" in method:
            method["metrics"] = [
                {k: v for k, v in metric.items() if k != "name"} for metric in method["metrics"]
            ]
        statement = (
            "实际计算声明：\n\n```json\n"
            + canonical_json(method)
            + "\n```\n\n"
            + display(payload["arithmetic"])
        )
    elif isinstance(claim, ExtremeClaim):
        fields = [claim.measure, *claim.labels]
        if set(fields) - set(columns) or len(set(claim.labels)) != len(claim.labels):
            raise ValueError("Unknown or duplicate ranking field")
        members: list[tuple[int, Decimal]] = []
        for index, row in enumerate(rows):
            value = row[claim.measure]
            if value is None:
                continue
            if isinstance(value, bool):
                raise ValueError("Ranking requires numeric values")
            try:
                number = Decimal(str(value))
            except Exception as exc:
                raise ValueError("Ranking requires numeric values") from exc
            if not number.is_finite():
                raise ValueError("Ranking requires finite values")
            members.append((index, number))
        if not members:
            statement = f"{display(labels.get(claim.measure, claim.measure))} 无可用数值，未形成极值结论。"
        else:
            extreme = (min if claim.operation == "min" else max)(value for _, value in members)
            indices = [i for i, value in members if value == extreme]
            word = "最小" if claim.operation == "min" else "最大"
            statement = (
                f"{scope}内，字段 {display(labels.get(claim.measure, claim.measure))} 的{word}值为 "
                f"{display(extreme)}。并列记录全部列示：\n\n"
            )
            shown = list(dict.fromkeys([*claim.labels, claim.measure]))
            statement += "\n\n".join(_table(rows[i], shown, labels) for i in indices)
            bindings = [
                {"row": i, "field": field, "value": rows[i][field]} for i in indices for field in shown
            ]
    else:
        if claim.row >= len(rows):
            raise ValueError("Claim row is outside stored result")
        fields = columns if isinstance(claim, RowClaim) else [claim.field]
        if set(fields) - set(columns):
            raise ValueError("Claim references an unknown field")
        statement = _table(rows[claim.row], fields, labels)
        bindings = [{"row": claim.row, "field": field, "value": rows[claim.row][field]} for field in fields]
    definition = {
        "declaration": claim.model_dump(),
        "sourceSha256": source["sha256"],
        "bindings": bindings,
        "scope": scope,
    }
    registered = bool(payload.get("metric_definitions") and payload.get("source", {}).get("metric_registry"))
    return {
        **definition,
        "claimId": fingerprint(definition)[:32],
        "level": "observed" if registered else "execution_output",
        "semanticCertification": (
            "registered BI contract"
            if registered
            else "Execution output only; business meaning not certified"
        ),
        "statement": statement,
        "sourceIds": [source["artifactId"]],
    }


def compile_report(
    store: ArtifactStore, run_id: str, agent_id: int, plan: ReportClaimsPlan, calculation_ids: list[str]
) -> tuple[str, dict[str, Any]]:
    manifest = store.load(run_id, agent_id)
    sources = [
        item
        for item in manifest["artifacts"]
        if item["kind"] == "query_result" or item["artifactId"] in calculation_ids
    ]
    allowed = {item["artifactId"] for item in sources}
    if any(claim.source_id not in allowed for claim in plan.claims) or any(
        action.source_id not in allowed for action in plan.followups
    ):
        raise ValueError("Report references unknown or unselected evidence")
    # Every source gets an explicit coverage entry even if the model omits it.
    lines = [
        "# 数据分析报告",
        "以下事实由保存结果直接生成；仅陈述本次查询范围，不自动确认因果根因。",
        "## 查询覆盖与指标口径",
    ]
    for item in sources:
        artifact = _result(store, run_id, agent_id, item["artifactId"])
        payload = artifact["content"]
        if artifact["kind"] == "query_result":
            parents = [store.get(run_id, agent_id, ref) for ref in artifact["parentIds"]]
            sql = next((parent["content"] for parent in parents if parent["kind"] == "sql"), {})
            if not sql.get("tables") and not payload.get("metric_definitions"):
                lines.append("此证据没有物理表来源，为表达式或常量查询输出，不能作为观测业务指标。")
            if not payload.get("metric_definitions"):
                lines.append("此证据属于通用 SQL 输出：仅核验返回单元格，指标业务语义需另行审核。")
        truncated = (
            "已截断，只覆盖返回行" if payload.get("truncated") else "未标记截断；业务覆盖仍由查询范围决定"
        )
        lines.append(f"- 证据 `{item['artifactId']}`：保存 {len(payload['data'])} 行；{truncated}。")
        if artifact["kind"] == "query_result":
            lines.extend(_execution_scope_lines(payload))
        for metric in payload.get("metric_definitions", []):
            denominator = metric["denominator"] if metric["denominator"] is not None else "不适用"
            lines.append(
                f"  - {display(metric['name'])}：单位 {display(metric['unit'])}；"
                f"粒度 {display(metric['grain'])}；版本 {display(metric['version'])}。"
            )
            lines.append(f"    计算分子 {display(metric['numerator'])}；分母 {display(denominator)}。")
            lines.append(
                f"    时间口径 {display(metric['time_policy'])}；空值规则 {display(metric['null_policy'])}。"
            )
    declarations = plan.claims
    if not declarations:
        declarations = [
            RowClaim(type="row", source_id=item["artifactId"], row=0)
            for item in sources
            if store.get(run_id, agent_id, item["artifactId"])["content"]["data"]
        ]
    claims = [compile_claim(store, run_id, agent_id, declaration) for declaration in declarations]
    lines.append("## 经程序核验的结果")
    lines.append("表内文本为原始字段标识或单元格内容，不构成已核验的业务定义或因果解释。")
    for claim in claims:
        lines.extend([claim["statement"], f"证据 `{claim['sourceIds'][0]}`；声明 `{claim['claimId']}`。"])
    if not claims:
        lines.append("结果为空，没有可展示的数值或排名结论。")
    lines.extend(
        [
            "## 方法与结论边界",
            "数值来自保存结果；派生计算必须可独立重算。静态导入、用户指定方法和失败尝试不能证明实际使用了算法。",
            "原始数据质量、真实运行时长、维修结束时间和工况控制未被上述汇总自动验证；无法据此确认工程根因或干预收益。",
        ]
    )
    if any(item["kind"] == "python_result" for item in manifest["artifacts"]):
        lines.append(
            "本运行包含 Python 输出；未转化为受控、可重算的计算证据的内容，"
            "不作为本报告已核验的数值或方法结论。"
        )
    if plan.followups:
        lines.append("## 待验证行动（不代表已确认根因）")
        for action in plan.followups:
            lines.append(f"- {FOLLOWUPS[action.action]} 依据 `{action.source_id}`。")
    return "\n\n".join(lines), {
        "schemaVersion": "2",
        "claims": claims,
        "followups": [a.model_dump() for a in plan.followups],
        "sourceIds": sorted(allowed),
        "publicationPolicy": "closed declarations; no model-authored factual prose",
    }
