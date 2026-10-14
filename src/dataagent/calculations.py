"""Declarative, local arithmetic over saved results; no SQL, eval or model-supplied values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dataagent.artifacts import ArtifactStore


class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Metric(StrictSpec):
    field: str
    operation: Literal["count", "count_distinct", "sum", "mean", "min", "max"]
    name: str = Field(min_length=1, max_length=80)


class RowFilter(StrictSpec):
    field: str
    operation: Literal["equals", "not_null"]
    value: str | int | float | bool | None = None


class AggregateSpec(StrictSpec):
    type: Literal["aggregate"]
    result_artifact_id: str
    group_by: list[str] = Field(default_factory=list, max_length=4)
    filters: list[RowFilter] = Field(default_factory=list, max_length=4)
    metrics: list[Metric] = Field(min_length=1, max_length=8)


class CompareSpec(StrictSpec):
    type: Literal["compare"]
    result_artifact_id: str
    group_by: list[str] = Field(default_factory=list, max_length=4)
    period_field: str
    baseline: str | int
    current: str | int
    measures: list[str] = Field(min_length=1, max_length=8)


CalculationSpec = Annotated[AggregateSpec | CompareSpec, Field(discriminator="type")]


class CalculationPlan(StrictSpec):
    calculations: list[CalculationSpec] = Field(default_factory=list, max_length=12)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Arithmetic requires numeric values, not booleans/objects")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Arithmetic requires numeric values") from exc
    if not result.is_finite():
        raise ValueError("Arithmetic requires finite values")
    return result


def _number(value: Decimal | None) -> str | None:
    # Exact decimal strings, not binary floats; rounding is a presentation choice.
    return None if value is None else format(value, "f")


def calculate(payload: dict[str, Any], spec: CalculationSpec) -> dict[str, Any]:
    columns, rows = payload["column"], payload["data"]
    referenced = set(spec.group_by)
    if len(referenced) != len(spec.group_by):
        raise ValueError("Duplicate grouping fields")
    if isinstance(spec, AggregateSpec):
        referenced.update(m.field for m in spec.metrics)
        referenced.update(f.field for f in spec.filters)
        names = [m.name for m in spec.metrics]
        if len(set(names + spec.group_by)) != len(names + spec.group_by):
            raise ValueError("Metric names must be unique and not replace grouping fields")
    else:
        referenced.update([spec.period_field, *spec.measures])
        if spec.period_field in spec.group_by or spec.baseline == spec.current:
            raise ValueError("Comparison needs distinct periods outside grouping fields")
        if set(spec.group_by) & {"measure", "baseline", "current", "difference", "change_pct", "status"}:
            raise ValueError("Grouping fields conflict with comparison output fields")
        if len(set(spec.measures)) != len(spec.measures):
            raise ValueError("Duplicate comparison measures")
    if referenced - set(columns):
        raise ValueError(f"Unknown result fields: {sorted(referenced - set(columns))}")
    if any(referenced - set(row) for row in rows):
        raise ValueError("Result rows do not match the declared columns")
    if isinstance(spec, AggregateSpec):
        rows = [
            row
            for row in rows
            if all(
                row[f.field] is not None if f.operation == "not_null" else row[f.field] == f.value
                for f in spec.filters
            )
        ]
    else:
        rows = [row for row in rows if row[spec.period_field] in (spec.baseline, spec.current)]
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row[field] for field in spec.group_by)
        groups.setdefault(key, []).append(row)
    if not spec.group_by and not groups:
        groups[()] = []
    output = []
    with localcontext() as context:
        context.prec = 28
        for key, members in groups.items():
            group = dict(zip(spec.group_by, key, strict=True))
            if isinstance(spec, AggregateSpec):
                result = dict(group)
                for metric in spec.metrics:
                    values = [row[metric.field] for row in members if row[metric.field] is not None]
                    if metric.operation in {"count", "count_distinct"}:
                        result[metric.name] = (
                            len(set(values)) if metric.operation == "count_distinct" else len(values)
                        )
                        continue
                    nums = [_decimal(value) for value in values]
                    value = None
                    if nums:
                        if metric.operation == "sum":
                            value = sum(nums, Decimal(0))
                        elif metric.operation == "mean":
                            value = sum(nums, Decimal(0)) / len(nums)
                        else:
                            value = (min if metric.operation == "min" else max)(nums)
                    result[metric.name] = _number(value)
                output.append(result)
            else:
                periods = {row[spec.period_field]: row for row in members}
                if len(periods) != len(members):
                    raise ValueError("Comparison requires one row per group and period; aggregate first")
                for field in spec.measures:
                    before = periods.get(spec.baseline, {}).get(field)
                    after = periods.get(spec.current, {}).get(field)
                    before = None if before is None else _decimal(before)
                    after = None if after is None else _decimal(after)
                    difference = None if before is None or after is None else after - before
                    rate = None if difference is None or before == 0 else difference / before * 100
                    status = (
                        "missing_period_or_value"
                        if difference is None
                        else "zero_baseline"
                        if before == 0
                        else "ok"
                    )
                    output.append(
                        {
                            **group,
                            "measure": field,
                            "baseline": _number(before),
                            "current": _number(after),
                            "difference": _number(difference),
                            "change_pct": _number(rate),
                            "status": status,
                        }
                    )
    output_columns = [
        *spec.group_by,
        *(
            [m.name for m in spec.metrics]
            if isinstance(spec, AggregateSpec)
            else ["measure", "baseline", "current", "difference", "change_pct", "status"]
        ),
    ]
    return {
        "spec": spec.model_dump(),
        "column": output_columns,
        "data": output,
        "sourceRows": len(payload["data"]),
        "matchedRows": len(rows),
        "rowCount": len(output),
        "truncated": bool(payload.get("truncated")),
        "scope": "All returned source rows only; never implies full-database coverage.",
        "arithmetic": (
            "Decimal; count excludes null, count_distinct deduplicates non-null; mean is unweighted; "
            "change_pct=(current-baseline)/baseline*100. Null is not zero."
        ),
    }


def calculate_result(
    store: ArtifactStore, run_id: str, agent_id: int, spec: CalculationSpec
) -> dict[str, Any]:
    source = store.get(run_id, agent_id, spec.result_artifact_id)
    if source["kind"] != "query_result":
        raise ValueError("Calculations require a stored query_result")
    content = calculate(source["content"], spec)
    artifact = store.add(run_id, agent_id, "result_calculation", content, [source["artifactId"]])
    return {**artifact, "content": content}
