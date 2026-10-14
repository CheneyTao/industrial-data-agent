from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from mcp import Client

import dataagent.mcp_server as mcp_module
from dataagent.analysis import AnalysisService
from dataagent.artifacts import ArtifactStore, fingerprint
from dataagent.errors import DataAgentError, NotFoundError, UnsafeSqlError
from dataagent.models import Agent, AgentDatasource, AgentDatasourceTable, Datasource
from dataagent.schemas import GraphEventType, GraphNodeResponse, GraphRequest, Plan, TextType
from dataagent.security import hash_api_key
from dataagent.visualization import ChartSpec, chart_from_result
from dataagent.workflow.nodes import DisplayStyleOutput, FeasibilityOutput


@pytest.fixture
async def analysis_case(app, tmp_path: Path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE readings (day TEXT, temperature REAL)")
        connection.executemany(
            "INSERT INTO readings VALUES (?, ?)", [("Mon", 10), ("Tue", 20), ("Wed", None)]
        )
        connection.execute("CREATE TABLE private_data (secret TEXT)")
    async with app.state.database.session_factory() as session:
        agent = Agent(name="Evidence test", status="published")
        other = Agent(name="Other agent", status="published")
        source = Datasource(
            name="Readings",
            type="sqlite",
            database_name=str(path),
            host="localhost",
            port=0,
            username="",
            password="",
            connection_url=f"sqlite:///{path.as_posix()}",
            status="active",
        )
        session.add_all([agent, other, source])
        await session.flush()
        link = AgentDatasource(agent_id=agent.id, datasource_id=source.id, is_active=1)
        session.add(link)
        await session.flush()
        session.add(AgentDatasourceTable(agent_datasource_id=link.id, table_name="readings"))
        await session.commit()
        await app.state.schema_indexer.initialize(session, agent.id)
        return agent.id, other.id


def test_artifact_integrity_ownership_and_paths(settings):
    store = ArtifactStore(settings)
    run = store.create(1, "Question")
    run_id = run["runId"]
    artifact = store.add(run_id, 1, "query_result", {"data": [1]}, [run["artifacts"][0]["artifactId"]])
    assert store.get(run_id, 1, artifact["artifactId"])["sha256"] == fingerprint({"data": [1]})
    with pytest.raises(NotFoundError):
        store.load(run_id, 2)
    with pytest.raises(DataAgentError):
        store.load("../outside", 1)
    with pytest.raises(DataAgentError):
        store.add(run_id, 1, "chart", {}, ["invented-id"])
    with pytest.raises(NotFoundError):
        store.get(run_id, 1, "../manifest")
    (store.root / run_id / f"{artifact['artifactId']}.json").write_text('{"data":[999]}', encoding="utf-8")
    with pytest.raises(DataAgentError, match="integrity"):
        store.get(run_id, 1, artifact["artifactId"])


@pytest.mark.parametrize("chart_type", ["column", "bar", "line", "pie", "scatter", "heatmap", "stacked_bar"])
def test_chart_types_are_built_from_results(chart_type):
    payload = {
        "column": ["x", "group", "a", "b"],
        "data": [{"x": 1, "group": "A", "a": 10, "b": 2}, {"x": 2, "group": "B", "a": 20, "b": 3}],
    }
    spec = ChartSpec(
        type=chart_type, x="x", category="group", y=["a", "b"] if chart_type == "stacked_bar" else ["a"]
    )
    option = chart_from_result(payload, spec)
    series = option["series"][0]
    if chart_type == "pie":
        assert [v["value"] for v in series["data"]] == [10, 20]
    elif chart_type == "heatmap":
        assert series["data"] == [[0, 0, 10], [1, 1, 20]]
    elif chart_type == "scatter":
        assert series["data"] == [[1, 10], [2, 20]]
    else:
        assert series["data"] == [10, 20]


def test_invalid_chart_never_silently_rewrites_data():
    data = {"column": ["x", "a"], "data": [{"x": "A", "a": None}, {"x": "B", "a": 20}]}
    assert chart_from_result(data, ChartSpec(type="line", x="x", y=["a"]))["series"][0]["data"] == [None, 20]
    for spec in [ChartSpec(type="pie", x="x", y=["a"]), ChartSpec(type="bar", x="x", y=["unknown"])]:
        with pytest.raises(ValueError):
            chart_from_result(data, spec)
    data["data"][0] = {"x": "B", "a": 10}
    with pytest.raises(ValueError, match="Repeated"):
        chart_from_result(data, ChartSpec(type="bar", x="x", y=["a"]))


async def test_query_allowlist_bound_and_evidence(app, analysis_case):
    agent_id, _ = analysis_case
    service = AnalysisService(app.state.settings)
    run_id = service.store.create(agent_id, "Read temperatures")["runId"]
    app.state.settings.sql_max_rows = 2
    async with app.state.database.session_factory() as session:
        for sql in [
            "SELECT * FROM private_data",
            "WITH readings AS (SELECT * FROM private_data) SELECT * FROM readings",
        ]:
            with pytest.raises(UnsafeSqlError):
                await service.execute(session, agent_id, run_id, sql)
        result = await service.execute(session, agent_id, run_id, "SELECT * FROM readings LIMIT 999999")
    assert result["rowCount"] == 2 and result["truncated"]
    assert "LIMIT 3" in result["provenance"]["sql"]
    proof = service.store.get(run_id, agent_id, result["provenance"]["artifactId"])
    assert proof["content"]["data"] == result["data"]
    assert proof["content"]["execution"]["executedAt"] == result["provenance"]["executedAt"]


async def test_trace_api_enforces_agent_api_key(app, client, analysis_case):
    agent_id, other_id = analysis_case
    store = ArtifactStore(app.state.settings)
    run_id = store.create(agent_id, "Protected")["runId"]
    async with app.state.database.session_factory() as session:
        agent = await session.get(Agent, agent_id)
        agent.api_key_enabled, agent.api_key = 1, hash_api_key("test-key")
        await session.commit()
    endpoint = f"/api/analysis/runs/{run_id}"
    assert (await client.get(endpoint, params={"agentId": agent_id})).status_code == 401
    assert (await client.get(endpoint, params={"agentId": other_id})).status_code == 404
    response = await client.get(endpoint, params={"agentId": agent_id}, headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["question"] == "Protected"


async def test_mcp_modular_query_chart_analysis_report(app, analysis_case, monkeypatch):
    agent_id, other_id = analysis_case
    monkeypatch.setattr(mcp_module, "get_settings", lambda: app.state.settings)
    monkeypatch.setattr(mcp_module, "get_database", lambda: app.state.database)
    async with Client(mcp_module.create_mcp_server()) as client:

        async def call(name, **args):
            response = await client.call_tool(name, {"agentId": agent_id, **args})
            assert not response.is_error, response.content
            return json.loads(response.content[0].text)

        run = await call("begin_analysis", question="Temperature comparison")
        run_id = run["runId"]
        await call("get_schema", runId=run_id)
        result = await call("execute_query", runId=run_id, sql="SELECT * FROM readings")
        result_id = result["provenance"]["artifactId"]
        chart = await call(
            "create_chart",
            runId=run_id,
            resultArtifactId=result_id,
            spec={"type": "bar", "x": "day", "y": ["temperature"]},
        )
        chart_id = chart["artifactId"]
        assert chart["content"]["option"]["series"][0]["data"] == [10, 20, None]
        premature = await client.call_tool("generate_report", {"agentId": agent_id, "runId": run_id})
        assert premature.is_error
        await call("analyze_result", runId=run_id, resultArtifactId=result_id, chartArtifactId=chart_id)
        calculation = await call(
            "calculate_result",
            runId=run_id,
            spec={
                "type": "aggregate",
                "result_artifact_id": result_id,
                "metrics": [{"field": "temperature", "operation": "mean", "name": "mean_temperature"}],
            },
        )
        assert calculation["content"]["data"] == [{"mean_temperature": "15.0"}]
        assert calculation["parentIds"] == [result_id]
        report = await call("generate_report", runId=run_id)
        assert result_id in report["content"] and chart_id in report["content"]
        trace = await call("get_run_trace", runId=run_id)
        assert trace["status"] == "succeeded"
        assert {a["kind"] for a in trace["artifacts"]} >= {
            "question",
            "schema",
            "sql",
            "query_result",
            "chart",
            "analysis",
            "report",
            "result_calculation",
        }
        wrong_agent = await client.call_tool(
            "get_artifact", {"agentId": other_id, "runId": run_id, "artifactId": result_id}
        )
        assert wrong_agent.is_error


@pytest.mark.parametrize("with_report", [False, True])
async def test_adaptive_workflow_and_durable_trace(app, analysis_case, with_report):
    agent_id, _ = analysis_case
    original = app.state.llm.complete
    steps = [
        {
            "step": 1,
            "tool_to_use": "SQL_GENERATE_NODE",
            "tool_parameters": {"instruction": "Read day and temperature from readings"},
        }
    ]
    if with_report:
        steps.append(
            {
                "step": 2,
                "tool_to_use": "REPORT_GENERATOR_NODE",
                "tool_parameters": {"summary_and_recommendations": "Compare the readings"},
            }
        )

    async def complete(session, **kwargs):
        if kwargs["purpose"] == "planner":
            return Plan(
                thought_process="Read selected data",
                deliverables=["result", "report"] if with_report else ["result"],
                execution_plan=steps,
            )
        if kwargs["purpose"] == "sql-generate":
            return "SELECT day, temperature FROM readings"
        if kwargs["purpose"] == "data-view":
            return DisplayStyleOutput(type="column", x="day", y=["temperature"])
        return await original(session, **kwargs)

    app.state.llm.complete = complete
    events = [
        e async for _, e in app.state.graph.stream(GraphRequest(agent_id=str(agent_id), query="比较温度"))
    ]
    assert not [e.text for e in events if e.error]
    assert any(e.event_type == GraphEventType.FINAL_ANSWER for e in events)
    assert any(e.node_name == "REPORT_GENERATOR_NODE" for e in events) == with_report
    run_id = events[0].thread_id
    trace = ArtifactStore(app.state.settings).load(run_id, agent_id)
    assert trace["status"] == "succeeded"
    assert any(a["kind"] == "query_result" for a in trace["artifacts"])
    if with_report:
        displays = [json.loads(e.text) for e in events if e.text_type == TextType.RESULT_SET]
        assert any(d.get("chartArtifactId") for d in displays)
        assert any(a["kind"] == "analysis" for a in trace["artifacts"])


async def test_clarification_is_preserved_and_not_marked_as_analysis_success(app, analysis_case):
    agent_id, _ = analysis_case
    original = app.state.llm.complete

    async def complete(session, **kwargs):
        if kwargs["purpose"] == "feasibility":
            return FeasibilityOutput(requirementType="NEED_CLARIFICATION", content="Which metric?")
        return await original(session, **kwargs)

    app.state.llm.complete = complete
    events = [
        e async for _, e in app.state.graph.stream(GraphRequest(agent_id=str(agent_id), query="Compare"))
    ]
    trace = ArtifactStore(app.state.settings).load(events[0].thread_id, agent_id)
    assert trace["status"] == "needs_clarification"
    assert any(a["kind"] == "answer" for a in trace["artifacts"])
    assert not any(a["kind"] == "query_result" for a in trace["artifacts"])


async def test_failed_chart_has_reason_and_partial_outcome(app, analysis_case):
    agent_id, _ = analysis_case
    assert app.state.settings.enrich_sql_result_timeout_ms is None
    original = app.state.llm.complete

    async def complete(session, **kwargs):
        if kwargs["purpose"] == "data-view":
            raise TimeoutError()
        return await original(session, **kwargs)

    app.state.llm.complete = complete
    events = [
        e async for _, e in app.state.graph.stream(GraphRequest(agent_id=str(agent_id), query="Compare"))
    ]
    store = ArtifactStore(app.state.settings)
    run_id = events[0].thread_id
    trace = store.load(run_id, agent_id)
    assert trace["status"] == "partial"
    status = next(a for a in trace["artifacts"] if a["kind"] == "chart_status")
    assert "TimeoutError" in store.get(run_id, agent_id, status["artifactId"])["content"]["reason"]
    assert any(a["kind"] == "query_result" for a in trace["artifacts"])


@pytest.mark.parametrize("status", ["needs_clarification", "waiting", "cancelled", "partial", "succeeded"])
async def test_mcp_complete_workflow_uses_persisted_status(app, analysis_case, monkeypatch, status):
    agent_id, _ = analysis_case
    monkeypatch.setattr(mcp_module, "get_settings", lambda: app.state.settings)
    monkeypatch.setattr(mcp_module, "get_database", lambda: app.state.database)

    async def stream(self, request):
        store = ArtifactStore(app.state.settings)
        run_id = store.create(agent_id, request.query)["runId"]
        yield (
            "message",
            GraphNodeResponse(
                agent_id=str(agent_id),
                thread_id=run_id,
                event_type=GraphEventType.FINAL_ANSWER,
                text="Outcome",
            ),
        )
        store.finish(run_id, agent_id, status)

    monkeypatch.setattr(mcp_module.GraphEngine, "stream", stream)
    async with Client(mcp_module.create_mcp_server()) as client:
        response = await client.call_tool("analyze_request", {"agentId": agent_id, "question": "Compare"})
        assert not response.is_error, response.content
        result = json.loads(response.content[0].text)
        assert result["status"] == status
        assert result["answer"] == "Outcome"
