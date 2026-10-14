from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from pydantic import Field, model_validator

from dataagent.artifacts import ArtifactStore, canonical_json
from dataagent.bi.contracts import Contract
from dataagent.errors import DataAgentError

EVIDENCE_LEVELS = (
    "observed",
    "association",
    "model_attribution",
    "causal_under_assumptions",
    "engineering_confirmed",
)


class DiagnosisRequest(Contract):
    agent_id: int = Field(ge=1)
    run_id: str
    result_artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    outcome_field: str | None = Field(None, min_length=1, max_length=128)
    exposure_field: str | None = Field(None, min_length=1, max_length=128)
    entity_field: str | None = Field(None, min_length=1, max_length=128)
    observed_at_field: str | None = Field(None, min_length=1, max_length=128)
    exposure_at_field: str | None = Field(None, min_length=1, max_length=128)
    outcome_at_field: str | None = Field(None, min_length=1, max_length=128)
    context_fields: list[str] = Field(default_factory=list, max_length=12)
    key_fields: list[str] = Field(default_factory=list, max_length=6)
    window_start: datetime | None = None
    window_end: datetime | None = None
    physical_root_cause: bool = False

    @model_validator(mode="after")
    def consistent_fields(self) -> DiagnosisRequest:
        if self.outcome_field and self.outcome_field == self.exposure_field:
            raise ValueError("outcome_field and exposure_field must differ")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("window_start and window_end must be supplied together")
        if self.window_start is not None and self.window_end is not None:
            if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
                raise ValueError("Diagnostic windows require timestamps with UTC offsets")
            if self.window_start >= self.window_end:
                raise ValueError("window_start must precede window_end")
        if len(set(self.key_fields)) != len(self.key_fields):
            raise ValueError("key_fields must be unique")
        if len(set(self.context_fields)) != len(self.context_fields):
            raise ValueError("context_fields must be unique")
        return self


def _binary(value: Any) -> int | None:
    # Do not reinterpret class labels, categories, thresholds, or NULL as events.
    if isinstance(value, (bool, int, float)) and value in (0, 1):
        return int(value)
    return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _wilson(events: int, total: int) -> tuple[float, float]:
    p, z = events / total, 1.959963984540054
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _association(
    rows: list[dict[str, Any]], outcome: str, exposure: str, independent: bool
) -> dict[str, Any]:
    groups = []
    for value in (1, 0):
        selected = [row for row in rows if _binary(row[exposure]) == value]
        events = sum(_binary(row[outcome]) == 1 for row in selected)
        total = len(selected)
        groups.append({"exposure": value, "events": events, "observations": total, "risk": events / total})
    exposed, control = groups
    p1, p0 = exposed["risk"], control["risk"]
    difference = p1 - p0
    interval = None
    if independent:
        l1, u1 = _wilson(exposed["events"], exposed["observations"])
        l0, u0 = _wilson(control["events"], control["observations"])
        interval = [
            max(-1.0, difference - math.sqrt((p1 - l1) ** 2 + (u0 - p0) ** 2)),
            min(1.0, difference + math.sqrt((u1 - p1) ** 2 + (p0 - l0) ** 2)),
        ]
    return {
        "method": "binary_exposure_risk_difference/v1",
        "unit": "returned observation",
        "groups": groups,
        "riskDifference": difference,
        "relativeRisk": p1 / p0 if p0 else None,
        "relativeRiskUnavailableReason": "Unexposed event risk is zero" if not p0 else None,
        "riskDifference95Interval": interval,
        "intervalMethod": "Newcombe difference of Wilson intervals" if interval else None,
        "intervalAssumptions": "Independent observational units; not a causal confidence interval",
        "interpretation": "未调整的观测关联；不能解释为干预效果、物理根因或总体故障率。",
    }


class DiagnosticsService:
    """Read-only, deterministic diagnostics over executed, owner-bound evidence.

    This service has no SQL executor, model call, or causal-claim promotion path.
    Binary association is calculated only after denominator and integrity gates.
    """

    def __init__(self, store: ArtifactStore):
        self.store = store

    def diagnose(self, request: DiagnosisRequest) -> dict[str, Any]:
        started = perf_counter()
        manifest = self.store.load(request.run_id, request.agent_id)
        result = self.store.get(request.run_id, request.agent_id, request.result_artifact_id)
        if result["kind"] != "query_result":
            raise DataAgentError("Diagnostics requires a persisted query_result artifact")
        succeeded = [
            step
            for step in manifest["steps"]
            if step["status"] == "succeeded"
            and step["node"] in {"execute_query", "SQL_EXECUTE_NODE", "BI_EXECUTE_NODE"}
            and request.result_artifact_id in step["outputArtifactIds"]
        ]
        if not succeeded:
            raise DataAgentError("Query result has no successful execution record")
        parents = [self.store.get(request.run_id, request.agent_id, ref) for ref in result["parentIds"]]
        sql_parents = [parent for parent in parents if parent["kind"] == "sql"]
        if not sql_parents:
            raise DataAgentError("Query result has no executed SQL provenance")
        payload = result["content"]
        if not isinstance(payload, dict) or payload.get("errorMsg"):
            raise DataAgentError("Query result is malformed or contains an execution error")
        columns, data = payload.get("column"), payload.get("data")
        if (
            not isinstance(columns, list)
            or not columns
            or any(not isinstance(column, str) or not column for column in columns)
            or len(set(columns)) != len(columns)
            or not isinstance(data, list)
            or any(not isinstance(row, dict) or set(row) != set(columns) for row in data)
        ):
            raise DataAgentError("Diagnostics requires complete, uniquely named tabular rows")
        if payload.get("rowCount") != len(data):
            raise DataAgentError("Stored rowCount does not match diagnostic observations")

        result_source = payload.get("source", {})
        if not isinstance(result_source, dict):
            raise DataAgentError("Stored query source metadata must be an object")
        sources = [result_source, *[p["content"] for p in sql_parents]]
        anonymous = any(
            isinstance(source, dict)
            and (
                source.get("dataset_id") == "scania_component_x"
                or source.get("sourceId") == "scania_component_x"
                or "scania" in str(source.get("sourceName", "")).casefold()
            )
            for source in sources
        )
        if request.physical_root_cause and anonymous:
            raise DataAgentError(
                "匿名 SCANIA 特征没有已确认的物理语义，不能进行物理根因确认", status_code=422
            )

        issues: list[dict[str, str]] = []
        missing: list[str] = []

        def issue(code: str, message: str, severity: Literal["blocking", "warning"] = "blocking") -> None:
            issues.append({"code": code, "severity": severity, "message": message})

        if not data:
            issue("empty_result", "查询结果为空；无法计算观测关联")
        if payload.get("truncated") is not False:
            issue("incomplete_result", "结果被截断或缺少完整性标记；不能使用部分结果计算风险分母")
            missing.append("未截断的完整目标观察单元查询结果")
        selected = [
            request.outcome_field,
            request.exposure_field,
            request.entity_field,
            request.observed_at_field,
            request.exposure_at_field,
            request.outcome_at_field,
            *request.key_fields,
            *request.context_fields,
        ]
        unknown = sorted(set(field for field in selected if field and field not in columns))
        if unknown:
            issue("missing_columns", "查询缺少指定字段：" + ", ".join(unknown))
            missing.append("补充查询字段：" + ", ".join(unknown))
        for role, field in (("outcome", request.outcome_field), ("exposure", request.exposure_field)):
            if not field:
                issue("missing_" + role, f"未指定 {role} 的二元 0/1 定义")
                missing.append(f"明确 {role} 的 0/1 事件定义及对应查询字段")
            elif field in columns:
                invalid = sum(_binary(row[field]) is None for row in data)
                if invalid:
                    issue(
                        "invalid_" + role, f"{field} 有 {invalid} 条 NULL 或非二元值；不会自动填 0 或重编码"
                    )
        null_counts = {column: sum(row[column] is None for row in data) for column in columns}
        keys = request.key_fields or (
            [request.entity_field, request.observed_at_field]
            if request.entity_field and request.observed_at_field
            else []
        )
        duplicate_keys = 0
        if keys and not (set(keys) - set(columns)):
            if any(null_counts[key] for key in keys):
                issue("null_keys", "观察单元键含 NULL；请修正来源或明确数据缺失策略")
            counts = Counter(canonical_json([row[key] for key in keys]) for row in data)
            duplicate_keys = sum(value - 1 for value in counts.values())
            if duplicate_keys:
                issue("duplicate_keys", f"有 {duplicate_keys} 条重复观察单元键，可能导致联表分母放大")
        else:
            issue("undefined_observation_key", "未提供可校验的观察单元键；关联仅描述返回行", "warning")
            missing.append("设备 ID + 观察时间，或明确且唯一的观察单元键")

        unique_entities = None
        independent = False
        if request.entity_field and request.entity_field in columns:
            entity_values = [row[request.entity_field] for row in data]
            unique_entities = len({canonical_json(value) for value in entity_values if value is not None})
            if any(value is None for value in entity_values):
                issue("null_entity", "设备/实体标识含 NULL；无法确定分析单元")
            independent = unique_entities == len(data) and not null_counts[request.entity_field]
            if not independent:
                issue("repeated_entities", "同一设备有多个观察；不输出基于独立行假设的置信区间", "warning")
        if not independent:
            missing.append("每设备独立观察单元，或考虑重复测量的聚类/时间序列估计方法")
        for field in request.context_fields:
            if field in columns and null_counts[field]:
                issue("missing_context", f"混杂候选字段 {field} 存在 NULL；本次不进行混杂调整", "warning")

        timing_valid, direction_violations = False, 0
        time_range = None
        if request.observed_at_field and request.observed_at_field in columns:
            parsed = [_timestamp(row[request.observed_at_field]) for row in data]
            if any(value is None for value in parsed):
                issue(
                    "invalid_observation_time",
                    "观察时间缺失、非日历时间或未说明时区，无法验证分析窗口",
                    "blocking" if request.window_start is not None else "warning",
                )
            elif parsed:
                time_range = {"start": min(parsed).isoformat(), "end": max(parsed).isoformat()}
                if request.window_start is not None and request.window_end is not None:
                    outside = sum(
                        not (request.window_start <= value < request.window_end) for value in parsed
                    )
                    if outside:
                        issue(
                            "outside_window",
                            f"{outside} 条观察不在指定左闭右开窗口内；请重新执行带窗口约束的查询",
                        )
        elif request.window_start is not None:
            issue("unverifiable_window", "指定了分析窗口但缺少观察时间，无法验证窗口分母")

        if (
            request.exposure_at_field
            and request.outcome_at_field
            and request.outcome_field
            and request.exposure_field
            and all(
                field in columns
                for field in (
                    request.exposure_at_field,
                    request.outcome_at_field,
                    request.outcome_field,
                    request.exposure_field,
                )
            )
        ):
            pairs = [
                row
                for row in data
                if _binary(row[request.exposure_field]) == 1 and _binary(row[request.outcome_field]) == 1
            ]
            times = [
                (_timestamp(row[request.exposure_at_field]), _timestamp(row[request.outcome_at_field]))
                for row in pairs
            ]
            if times and all(start is not None and end is not None for start, end in times):
                direction_violations = sum(start >= end for start, end in times)
                timing_valid = direction_violations == 0
                if direction_violations:
                    issue("reversed_temporality", "暴露与结局同时或晚于结局，不能据此推断原因方向", "warning")
            else:
                issue("unavailable_temporality", "缺少可比的暴露/结局事件时间，不能检查时间先后", "warning")
        if not timing_valid:
            missing.append("带时区的暴露起点与结局事件时间，且暴露必须先于结局")

        association = None
        blocked = any(item["severity"] == "blocking" for item in issues)
        if not blocked:
            group_sizes = Counter(_binary(row[request.exposure_field]) for row in data)
            if not group_sizes[0] or not group_sizes[1]:
                issue("empty_comparison_group", "暴露组或未暴露组的分母为 0；关联差值不可计算")
                missing.append("在同一合格观察窗口内具有暴露与未暴露两组的可比观察单元")
            else:
                association = _association(data, request.outcome_field, request.exposure_field, independent)
        missing.extend(
            [
                "设备运行时长/负荷、维护记录等业务混杂因素及选样/删失机制",
                "明确的因果问题、因果图、识别假设和受控估计/反驳检验",
                "现场检测、工程专家复核或干预后验证证据（用于根因确认）",
            ]
        )
        if request.window_start is None:
            missing.append("显式目标观察窗口及其暴露时长/在险时间口径")
        if anonymous:
            missing.append("匿名字段的已确认物理映射及时间单位；无法根据字段名猜测物理故障")
        if request.physical_root_cause:
            issue(
                "engineering_confirmation_unavailable",
                "自动诊断不提供工程确认；须关联独立现场/干预证据",
                "warning",
            )

        content = {
            "version": "1",
            "request": request.model_dump(mode="json"),
            "evidenceLevel": "association" if association else "observed",
            "evidenceLevels": list(EVIDENCE_LEVELS),
            "qualityGate": {
                "status": "blocked" if any(item["severity"] == "blocking" for item in issues) else "passed",
                "rowCount": len(data),
                "nullCounts": null_counts,
                "keyFields": keys,
                "duplicateKeys": duplicate_keys,
                "issues": issues,
            },
            "observed": {
                "records": len(data),
                "uniqueEntities": unique_entities,
                "observationTimeRange": time_range,
                "queryScope": payload.get("scope", result_source.get("filters")),
                "scopeNotice": "分析仅限已保存查询结果及其选样范围，不代表完整总体或运行时长故障率。",
            },
            "association": association,
            "causalReadiness": {
                "status": "not_identified",
                "timingChecked": timing_valid,
                "directionViolations": direction_violations,
                "confoundingAdjusted": False,
                "modelAttributionExecuted": False,
                "causalEstimatorExecuted": False,
                "engineeringConfirmed": False,
                "reason": "观测关联不满足因果识别或工程确认；本服务不会自动提升证据等级。",
            },
            "candidateHypotheses": (
                [
                    {
                        "statement": (
                            f"{request.exposure_field} 与 {request.outcome_field} "
                            "的观测比例差异值得进一步核查"
                        ),
                        "status": "requires_validation",
                        "evidenceLevel": "association",
                        "nextChecks": [
                            "确认观察单元与窗口",
                            "检查负荷/设备年龄/维护等混杂",
                            "核查时间先后并进行现场或干预验证",
                        ],
                    }
                ]
                if association
                else []
            ),
            "missingInputs": list(dict.fromkeys(missing)),
            "methodExecution": {
                "operator": association["method"] if association else "diagnostic_data_quality/v1",
                "executed": True,
                "inputArtifactId": request.result_artifact_id,
                "excludedRows": 0,
                "automaticImputation": False,
            },
            "provenance": {
                "runId": request.run_id,
                "resultArtifactId": request.result_artifact_id,
                "resultSha256": result["sha256"],
                "resultCreatedAt": result["createdAt"],
                "sqlArtifactIds": [parent["artifactId"] for parent in sql_parents],
                "source": sources,
                "executionNodes": [step["node"] for step in succeeded],
            },
        }
        artifact = self.store.add(
            request.run_id, request.agent_id, "diagnosis", content, [request.result_artifact_id]
        )
        self.store.step(
            request.run_id,
            request.agent_id,
            "diagnose_result",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
            inputs=[request.result_artifact_id],
            outputs=[artifact["artifactId"]],
        )
        return {**artifact, "content": content}
