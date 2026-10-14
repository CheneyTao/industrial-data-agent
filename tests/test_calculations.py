from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dataagent.artifacts import ArtifactStore
from dataagent.calculations import AggregateSpec, CalculationPlan, CompareSpec, calculate, calculate_result
from dataagent.errors import NotFoundError
from dataagent.reports import prepare_report_calculations, report_evidence


def aggregate(**extra):
    return AggregateSpec.model_validate(
        {
            "type": "aggregate",
            "result_artifact_id": "source",
            "group_by": ["machine"],
            "metrics": [
                {"field": "fault", "operation": "count", "name": "faults"},
                {"field": "order", "operation": "count", "name": "orders"},
                {"field": "order", "operation": "count_distinct", "name": "distinct_orders"},
                {"field": "cost", "operation": "sum", "name": "cost_sum"},
            ],
            **extra,
        }
    )


@pytest.fixture
def union_rows():
    return {
        "column": ["machine", "fault", "order", "cost"],
        "truncated": True,
        "data": [
            {"machine": "A", "fault": "f1", "order": None, "cost": None},
            {"machine": "A", "fault": "f2", "order": None, "cost": None},
            {"machine": "A", "fault": None, "order": "w1", "cost": "0.1"},
            {"machine": "A", "fault": None, "order": "w1", "cost": "0.2"},
            {"machine": "B", "fault": "f3", "order": None, "cost": None},
        ],
    }


def test_union_nulls_count_distinct_exact_decimal_and_scope(union_rows):
    result = calculate(union_rows, aggregate())
    assert result["data"] == [
        {"machine": "A", "faults": 2, "orders": 2, "distinct_orders": 1, "cost_sum": "0.3"},
        {"machine": "B", "faults": 1, "orders": 0, "distinct_orders": 0, "cost_sum": None},
    ]
    assert result["truncated"] and result["sourceRows"] == result["matchedRows"] == 5


def test_filters_and_statistics_keep_source_unchanged(union_rows):
    result = calculate(
        union_rows,
        aggregate(
            filters=[{"field": "order", "operation": "not_null"}],
            metrics=[{"field": "cost", "operation": op, "name": op} for op in ["mean", "min", "max"]],
        ),
    )
    assert result["data"] == [{"machine": "A", "mean": "0.15", "min": "0.1", "max": "0.2"}]
    assert result["matchedRows"] == 2 and len(union_rows["data"]) == 5


@pytest.mark.parametrize("value", [True, "not-numeric", "NaN", "Infinity", {}, []])
def test_arithmetic_rejects_non_numeric_values(union_rows, value):
    union_rows["data"][2]["cost"] = value
    with pytest.raises(ValueError, match="numeric|finite"):
        calculate(union_rows, aggregate())


def test_unknown_fields_and_duplicate_names_fail(union_rows):
    with pytest.raises(ValueError, match="Unknown"):
        calculate(union_rows, aggregate(group_by=["guessed_field"]))
    with pytest.raises(ValueError, match="unique"):
        calculate(union_rows, aggregate(metrics=[{"field": "cost", "operation": "sum", "name": "machine"}]))


def comparison():
    return CompareSpec(
        type="compare",
        result_artifact_id="source",
        group_by=["group"],
        period_field="period",
        baseline="before",
        current="after",
        measures=["value"],
    )


def test_comparison_zero_null_missing_and_duplicate_periods():
    rows = [
        {"group": "A", "period": "before", "value": "0.1"},
        {"group": "A", "period": "after", "value": "0.3"},
        {"group": "B", "period": "before", "value": 0},
        {"group": "B", "period": "after", "value": 5},
        {"group": "C", "period": "after", "value": None},
    ]
    payload = {"column": ["group", "period", "value"], "data": rows}
    results = calculate(payload, comparison())["data"]
    assert results[0]["difference"] == "0.2" and results[0]["change_pct"] == "200"
    assert results[1]["difference"] == "5" and results[1]["change_pct"] is None
    assert results[1]["status"] == "zero_baseline"
    assert results[2]["status"] == "missing_period_or_value"
    rows.append(rows[0])
    with pytest.raises(ValueError, match="one row"):
        calculate(payload, comparison())


async def test_calculation_plan_repair_is_bounded_and_stored(settings, union_rows):
    store = ArtifactStore(settings)
    run_id = store.create(1, "Count events and sum costs")["runId"]
    sql = store.add(run_id, 1, "sql", {"sql": "select * from demo", "tables": ["demo"], "sourceName": "demo"})
    source = store.add(run_id, 1, "query_result", union_rows, [sql["artifactId"]])
    aid = source["artifactId"]
    llm = SimpleNamespace(
        settings=settings,
        complete=AsyncMock(
            side_effect=[
                CalculationPlan(calculations=[aggregate(result_artifact_id=aid, group_by=["unknown"])]),
                CalculationPlan(calculations=[aggregate(result_artifact_id=aid)]),
            ]
        ),
    )
    await prepare_report_calculations(store, run_id, 1, None, llm, "Count events and sum costs")
    manifest = store.load(run_id, 1)
    assert [a["kind"] for a in manifest["artifacts"]].count("calculation_plan") == 2
    derived = next(a for a in manifest["artifacts"] if a["kind"] == "result_calculation")
    assert derived["parentIds"] == [aid]
    pack = report_evidence(store, run_id, 1)
    assert len(pack["results"][0]["rows"]) == 5
    assert pack["calculations"][0]["rows"][0] == ["A", 2, 2, 1, "0.3"]
    scoped = report_evidence(store, run_id, 1, calculation_ids=[])
    assert not scoped["calculations"] and len(scoped["results"][0]["rows"]) == 5
    assert "Unknown result fields" in llm.complete.call_args_list[1].kwargs["user"]
    with pytest.raises(NotFoundError):
        calculate_result(store, run_id, 2, aggregate(result_artifact_id=aid))
