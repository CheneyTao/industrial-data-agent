from __future__ import annotations

import pytest
from pydantic import ValidationError

from dataagent.artifacts import ArtifactStore
from dataagent.calculations import AggregateSpec, Metric, calculate_result
from dataagent.report_claims import (
    CellClaim,
    ExtremeClaim,
    MethodClaim,
    ReportClaimsPlan,
    compile_claim,
    compile_report,
    display,
)


def fixture(settings, truncated=False, source=None, question="comparison"):
    store = ArtifactStore(settings)
    run = store.create(1, question)["runId"]
    sql = store.add(
        run,
        1,
        "sql",
        {"sql": "SELECT label,value FROM facts", "sourceId": 1, "sourceName": "test", "tables": ["facts"]},
    )
    result = store.add(
        run,
        1,
        "query_result",
        {
            "column": ["label", "value"],
            "data": [
                {"label": "A", "value": 7},
                {"label": "B", "value": 7},
                {"label": "C", "value": None},
                {"label": "D", "value": 0},
            ],
            "truncated": truncated,
            "rowCount": 4,
            **({"source": source} if source is not None else {}),
        },
        [sql["artifactId"]],
    )
    store.step(
        run,
        1,
        "execute_query",
        status="succeeded",
        duration_ms=1,
        inputs=[sql["artifactId"]],
        outputs=[result["artifactId"]],
    )
    return store, run, result["artifactId"]


@pytest.mark.parametrize("extra", [{"value": 999}, {"text": "confirmed cause"}, {"method": "DoWhy"}])
def test_model_cannot_supply_values_facts_or_method_names(extra):
    with pytest.raises(ValidationError):
        ReportClaimsPlan.model_validate(
            {"claims": [{"type": "cell", "source_id": "known", "row": 0, "field": "value", **extra}]}
        )


def test_rank_includes_all_ties_and_null_is_not_zero(settings):
    store, run, source = fixture(settings)
    claim = compile_claim(
        store,
        run,
        1,
        ExtremeClaim(type="extremum", source_id=source, measure="value", labels=["label"], operation="max"),
    )
    assert {b["value"] for b in claim["bindings"] if b["field"] == "label"} == {"A", "B"}
    assert "业务总体" in claim["scope"]
    missing = compile_claim(store, run, 1, CellClaim(type="cell", source_id=source, row=2, field="value"))
    zero = compile_claim(store, run, 1, CellClaim(type="cell", source_id=source, row=3, field="value"))
    assert missing["bindings"][0]["value"] is None and "NULL" in missing["statement"]
    assert zero["bindings"][0]["value"] == 0 and "NULL" not in zero["statement"]


def test_truncation_scope_cannot_be_overridden(settings):
    store, run, source = fixture(settings, truncated=True)
    text, compiled = compile_report(
        store,
        run,
        1,
        ReportClaimsPlan(
            claims=[ExtremeClaim(type="extremum", source_id=source, measure="value", operation="max")]
        ),
        [],
    )
    assert "结果已截断，不代表全量" in text
    assert compiled["claims"][0]["level"] == "execution_output"


def test_method_requires_independent_reproduction_and_count_is_not_distinct(settings):
    store, run, source = fixture(settings)
    spec = AggregateSpec(
        type="aggregate",
        result_artifact_id=source,
        metrics=[
            Metric(field="value", operation="count", name="non_null"),
            Metric(field="value", operation="count_distinct", name="distinct"),
        ],
    )
    calc = calculate_result(store, run, 1, spec)
    method = compile_claim(store, run, 1, MethodClaim(type="method", source_id=calc["artifactId"]))
    assert "count_distinct" in method["statement"]
    assert calc["content"]["data"] == [{"non_null": 3, "distinct": 2}]
    bad = {**calc["content"], "data": [{"non_null": 100, "distinct": 100}]}
    forged = store.add(run, 1, "result_calculation", bad, [source])
    with pytest.raises(ValueError, match="reproduced"):
        compile_claim(
            store, run, 1, CellClaim(type="cell", source_id=forged["artifactId"], row=0, field="non_null")
        )
    with pytest.raises(ValueError, match="Method assertions"):
        compile_claim(store, run, 1, MethodClaim(type="method", source_id=source))


def test_source_text_is_inert_and_agents_do_not_share_evidence(settings):
    store, run, source = fixture(settings)
    with pytest.raises(Exception, match="this agent"):
        compile_claim(store, run, 2, CellClaim(type="cell", source_id=source, row=0, field="value"))
    from dataagent.report_claims import display

    assert "<script>" not in display("<script>alert(1)</script>|`fake`")
    assert "\\|" in display("a|b")
    assert "![" not in display("![tracking](https://example.invalid/pixel)")
    assert display("count(value)") == "count&#40;value&#41;"


def test_calculation_alias_cannot_become_a_factual_label(settings):
    store, run, source = fixture(settings)
    label = "已确认高温根因，故障率99%"
    calc = calculate_result(
        store,
        run,
        1,
        AggregateSpec(
            type="aggregate",
            result_artifact_id=source,
            metrics=[Metric(field="value", operation="count", name=label)],
        ),
    )
    row = compile_claim(
        store, run, 1, CellClaim(type="cell", source_id=calc["artifactId"], row=0, field=label)
    )
    method = compile_claim(store, run, 1, MethodClaim(type="method", source_id=calc["artifactId"]))
    assert label not in row["statement"] and label not in method["statement"]
    assert "count(value)" not in row["statement"]  # Markdown delimiters are encoded.
    assert "count" in row["statement"]


def test_report_rejects_result_without_successful_execution(settings):
    store = ArtifactStore(settings)
    run = store.create(1, "unfinished")["runId"]
    sql = store.add(run, 1, "sql", {"sql": "SELECT 1"})
    result = store.add(
        run,
        1,
        "query_result",
        {"column": ["value"], "data": [{"value": 1}], "rowCount": 1, "truncated": False},
        [sql["artifactId"]],
    )
    with pytest.raises(ValueError, match="successful execution"):
        compile_claim(
            store, run, 1, CellClaim(type="cell", source_id=result["artifactId"], row=0, field="value")
        )


def test_report_displays_actual_execution_dates_and_device_not_requested_scope(settings):
    store, run, _ = fixture(
        settings,
        source={
            "dataset_id": "industrial_pdm",
            "scope": {"startDate": "2099-01-01", "machineId": 999},
            "metric_registry": {
                "execution": {
                    "scope": {
                        "startDate": "2015-01-01",
                        "endDate": "2015-01-31",
                        "machineId": 42,
                        "model": "model2",
                        "failureType": None,
                    }
                }
            },
        },
        question="分析 2099 年设备 999，全厂范围",
    )
    text, _ = compile_report(store, run, 1, ReportClaimsPlan(), [])
    assert f"实际执行数据集：{display('industrial_pdm')}" in text
    assert "开始日期：2015-01-01" in text and "结束日期：2015-01-31" in text
    assert "设备编号：42" in text and "设备型号：model2" in text
    assert "故障类型：NULL（未设置）" in text
    assert "2099-01-01" not in text and "设备编号：999" not in text and "全厂" not in text


def test_report_displays_saved_source_scope_without_registry(settings):
    store, run, _ = fixture(
        settings,
        source={
            "dataset_id": "industrial_pdm",
            "scope": {"startDate": "2015-03-01", "endDate": "2015-03-31", "machineId": 7},
        },
    )
    text, _ = compile_report(store, run, 1, ReportClaimsPlan(), [])
    assert "开始日期：2015-03-01" in text and "结束日期：2015-03-31" in text
    assert "设备编号：7" in text


def test_report_displays_saved_scania_filters_without_inventing_calendar_time(settings):
    store, run, _ = fixture(
        settings,
        source={
            "dataset_id": "scania_component_x",
            "time_kind": "relative",
            "filters": {
                "split": "train",
                "vehicle_id": 7,
                "min_time": 0,
                "max_time": 24,
                "specifications": {"Spec_0": "A"},
                "class_label": None,
            },
        },
    )
    text, _ = compile_report(store, run, 1, ReportClaimsPlan(), [])
    assert "数据分区：train" in text and "车辆编号：7" in text
    assert "相对时间下限（非日历日期）：0" in text
    assert "相对时间上限（非日历日期）：24" in text
    assert f"规格筛选：{display({'Spec_0': 'A'})}" in text
    assert "类别标签：NULL（未设置）" in text
    assert "开始日期：" not in text and "小时" not in text


def test_report_execution_scope_strings_and_unknown_keys_remain_inert(settings):
    injected = "<script>alert(1)</script>|`scope`\n![tracking](https://example.invalid/pixel)"
    store, run, _ = fixture(
        settings,
        source={
            "dataset_id": injected,
            "metric_registry": {"execution": {"scope": {"model": injected, injected: [injected]}}},
        },
    )
    text, _ = compile_report(store, run, 1, ReportClaimsPlan(), [])
    assert f"实际执行数据集：{display(injected)}" in text
    assert f"设备型号：{display(injected)}" in text
    assert f"{display(injected)}：{display([injected])}" in text
    assert "<script>" not in text and "![tracking]" not in text and "`scope`" not in text


def test_report_without_saved_scope_requires_execution_sql_not_question_inference(settings):
    store, run, _ = fixture(settings, question="请分析 2099 年设备 999 的全厂范围")
    text, _ = compile_report(store, run, 1, ReportClaimsPlan(), [])
    assert "未保存结构化执行范围，请查看该证据的执行 SQL 与参数" in text
    assert "实际执行筛选：" not in text
    assert "2099" not in text and "999" not in text and "全厂" not in text
