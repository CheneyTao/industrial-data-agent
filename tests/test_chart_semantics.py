from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from dataagent.analysis import AnalysisService
from dataagent.visualization import ChartSpec, chart_from_result, result_profile


def test_model_schema_never_exposes_axis_names():
    fields = ChartSpec.model_json_schema()["properties"]
    assert {"dimension", "measures", "group_by"} <= fields.keys()
    assert not {"x", "y", "category"} & fields.keys()
    old = ChartSpec(type="bar", x="machine", y=["hours"])
    assert old.model_dump()["dimension"] == "machine"
    assert old.legacy_display()["x"] == "machine"


def test_bar_semantic_roles_and_no_silent_swap():
    data = {
        "column": ["machine", "hours"],
        "data": [{"machine": "A", "hours": 1}, {"machine": "B", "hours": 2}],
    }
    good = ChartSpec(type="bar", dimension="machine", measures=["hours"])
    option = chart_from_result(data, good)
    assert option["yAxis"]["data"] == ["A", "B"] and option["xAxis"]["type"] == "value"
    with pytest.raises(ValueError, match="numeric"):
        chart_from_result(data, ChartSpec(type="bar", dimension="hours", measures=["machine"]))


def test_long_form_trend_preserves_groups_missing_values_and_order():
    data = {
        "column": ["month", "model", "oee"],
        "data": [
            {"month": "07", "model": "A", "oee": 91},
            {"month": "07", "model": "B", "oee": 92},
            {"month": "08", "model": "A", "oee": 93},
        ],
    }
    spec = ChartSpec(type="line", dimension="month", group_by="model", measures=["oee"])
    option = chart_from_result(data, spec)
    assert option["xAxis"]["data"] == ["07", "08"]
    assert option["series"][0]["data"] == [91, 93]
    assert option["series"][1]["data"] == [92, None]
    assert result_profile(data)["fields"]["month"]["distinctCount"] == 2
    data["data"].append(data["data"][0])
    with pytest.raises(ValueError, match="Repeated"):
        chart_from_result(data, spec)


def test_multiple_units_never_share_a_numeric_axis():
    data = {
        "column": ["machine", "hours", "cost"],
        "data": [
            {"machine": "A", "hours": 1, "cost": 1000},
            {"machine": "B", "hours": 2, "cost": 2000},
        ],
    }
    spec = ChartSpec(type="bar", dimension="machine", measures=["hours", "cost"])
    option = chart_from_result(data, spec)
    assert len(option["grid"]) == len(option["xAxis"]) == len(option["yAxis"]) == 2
    assert option["series"][0]["xAxisIndex"] == 0 and option["series"][1]["xAxisIndex"] == 1
    assert option["series"][0]["data"] == [1, 2] and option["series"][1]["data"] == [1000, 2000]


async def test_invalid_proposal_gets_original_data_feedback_and_trace(settings):
    service = AnalysisService(settings)
    run_id = service.store.create(1, "Compare machines")["runId"]
    result = service.store.add(
        run_id,
        1,
        "query_result",
        {
            "column": ["machine", "hours"],
            "data": [{"machine": "A", "hours": 1}, {"machine": "B", "hours": 2}],
        },
    )
    llm = AsyncMock()
    llm.complete.side_effect = [
        ChartSpec(type="bar", dimension="hours", measures=["machine"]),
        ChartSpec(type="bar", dimension="machine", measures=["hours"]),
    ]
    spec = await service.choose_chart(None, llm, 1, run_id, result["artifactId"], "Compare machines")
    assert llm.complete.call_count == 2
    second = llm.complete.call_args.kwargs["user"]
    assert "Compare machines" in second and "validationError" in second and "profile" in second
    chart = service.chart(1, run_id, result["artifactId"], spec)
    decisions = [
        item for item in service.store.load(run_id, 1)["artifacts"] if item["kind"] == "chart_decision"
    ]
    assert len(decisions) == 2
    assert all(item["artifactId"] in chart["parentIds"] for item in decisions)
    assert "rejected" in json.dumps(service.store.get(run_id, 1, decisions[0]["artifactId"]))
