from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dataagent.bi.contracts import BIPlan, BIRequest, ScaniaFilters
from dataagent.bi.scania import ScaniaComponentXAdapter
from dataagent.bi.service import Interpretation, PlannerDecision
from dataagent.bi.tasks import TERMINAL, TaskManager
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.models import Agent
from dataagent.schemas import GraphRequest


@pytest.fixture
def scania_files(tmp_path: Path) -> Path:
    root = tmp_path / "scania"
    raw = root / "raw"
    raw.mkdir(parents=True)

    def write(name, columns, rows):
        with (raw / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)

    for split in ["train", "validation", "test"]:
        write(
            f"{split}_specifications",
            ["vehicle_id", *(f"Spec_{i}" for i in range(8))],
            [[1, *(["A"] * 8)], [2, *(["B"] * 8)], [3, *(["A"] * 8)]],
        )
        write(
            f"{split}_operational_readouts",
            ["vehicle_id", "time_step", "171_0", "666_0"],
            [[1, 0, 10, 20], [1, 20, 20, 40], [2, 10, 30, 60], [3, 40, "", 80]],
        )
        if split == "train":
            write(
                "train_tte",
                ["vehicle_id", "in_study_repair", "length_of_study_time_step"],
                [[1, 1, 25], [2, 0, 50], [3, 0, 60]],
            )
        else:
            write(f"{split}_labels", ["vehicle_id", "class_label"], [[1, 4], [2, 0], [3, 2]])
    return root


@pytest.fixture
def scania(settings, scania_files):
    settings.scania_data_dir = scania_files
    return ScaniaComponentXAdapter(settings)


async def test_dashboard_has_correct_grains_and_censoring(scania):
    result = await scania.dashboard({"split": "train"}, "171_0")
    overview = result["overview"]
    assert overview["vehicle_count"] == 3
    assert overview["readout_count"] == 4
    assert overview["repair_vehicle_count"] == 1
    assert overview["censored_vehicle_count"] == 2
    assert overview["feature_missing_pct"] == 25
    assert overview["repair_vehicle_share_pct"] == pytest.approx(100 / 3)
    assert sum(row["vehicle_count"] for row in result["duration"]) == 3
    labels = await scania.dashboard({"split": "validation"})
    assert labels["overview"]["label_window_vehicle_count"] == 2
    assert "repair_vehicle_count" not in labels["overview"]


async def test_filters_parameterized_empty_and_entity_semantics(scania):
    result = await scania.dashboard({"min_time": 5, "max_time": 25})
    assert result["overview"]["vehicle_count"] == 2
    assert result["overview"]["readout_count"] == 2
    empty = await scania.dashboard({"specifications": {"Spec_0": "A' OR 1=1 --"}})
    assert empty["overview"]["vehicle_count"] == 0
    assert empty["overview"]["feature_missing_pct"] is None
    assert empty["trend"] == []
    with pytest.raises(NotFoundError):
        await scania.entity(999, {}, "171_0")
    detail = await scania.entity(1, {}, "171_0")
    assert [r["value"] for r in detail["points"]] == [10, 20]


@pytest.mark.parametrize(
    "filters",
    [
        {"start_date": "2020-01-01"},
        {"min_time": 5, "max_time": 4},
        {"min_time": float("nan")},
        {"class_label": 2},
        {"split": "test", "in_study_repair": 1},
        {"specifications": {"temperature": "hot"}},
    ],
)
def test_invalid_relative_time_and_schema_rejected(filters):
    with pytest.raises(ValidationError):
        ScaniaFilters.model_validate(filters)


@pytest.mark.parametrize(
    "arguments",
    [
        {"metric_ids": ["failure_rate"]},
        {"metric_ids": ["vehicle_count"], "dimension": "temperature"},
        {"metric_ids": ["vehicle_count", "readout_count"]},
        {"metric_ids": ["repair_vehicle_count"], "filters": {"split": "test"}},
        {"metric_ids": ["vehicle_count"], "intent": "correlation"},
    ],
)
def test_invalid_bi_metric_plans_rejected(scania, arguments):
    with pytest.raises(DataAgentError):
        scania.validate(BIPlan(dataset_id="scania_component_x", **arguments))


async def test_aggregate_trend_correlation_and_periods(scania):
    result = await scania.query(
        BIPlan(dataset_id=scania.dataset_id, metric_ids=["vehicle_count"], dimension="Spec_0")
    )
    assert result["data"] == [{"Spec_0": "A", "vehicle_count": 2}, {"Spec_0": "B", "vehicle_count": 1}]
    result = await scania.query(
        BIPlan(
            dataset_id=scania.dataset_id,
            intent="trend",
            metric_ids=["readout_count"],
            dimension="time_bucket",
        )
    )
    assert [r["readout_count"] for r in result["data"]] == [2, 1, 1]
    result = await scania.query(
        BIPlan(dataset_id=scania.dataset_id, intent="correlation", metric_ids=["mean_171_0", "mean_666_0"])
    )
    assert result["statistics"]["pearson_r"] == pytest.approx(1)
    assert result["statistics"]["paired_observations"] == 3
    result = await scania.query(
        BIPlan(
            dataset_id=scania.dataset_id,
            intent="period_comparison",
            metric_ids=["readout_count"],
            filters={"min_time": 0, "max_time": 40},
        )
    )
    assert {r["period"]: r["readout_count"] for r in result["data"]} == {"first_half": 2, "second_half": 2}


async def test_duckdb_timeout_interrupt_releases_slot(scania):
    scania.settings.bi_query_timeout_seconds = 0.1
    started = perf_counter()
    with pytest.raises(DataAgentError, match="超时"):
        await scania.run(
            ScaniaFilters(),
            lambda c: c.execute(
                "SELECT SUM(a.i*b.i) FROM range(1000000) a(i), range(1000000) b(i)"
            ).fetchall(),
        )
    assert perf_counter() - started < 5
    scania.settings.bi_query_timeout_seconds = 5
    assert (await scania.dashboard({}))["overview"]["vehicle_count"] == 3


async def provision(app, scania_files):
    app.state.settings.scania_data_dir = scania_files
    app.state.graph.catalog.adapters["scania_component_x"] = ScaniaComponentXAdapter(app.state.settings)
    async with app.state.database.session_factory() as session:
        agent = Agent(name="BI test", status="published")
        session.add(agent)
        await session.commit()
        return agent.id


async def test_graph_bi_reuses_evidence_chart_and_followup(app, scania_files, monkeypatch):
    agent_id = await provision(app, scania_files)
    original = app.state.llm.complete

    async def complete(*args, **kwargs):
        if kwargs.get("purpose") == "bi-interpret":
            content = json.loads(kwargs["user"])
            return Interpretation(
                observations=[{"text": "比较结果来自已注册指标。", "evidence_ids": [content["evidence_id"]]}]
            )
        return await original(*args, **kwargs)

    monkeypatch.setattr(app.state.llm, "complete", complete)
    plan = BIPlan(
        dataset_id="scania_component_x",
        intent="ranking",
        metric_ids=["vehicle_count"],
        dimension="vehicle_id",
    )
    payload = BIRequest(agent_id=agent_id, question="按车辆统计观测", dataset_id=plan.dataset_id, plan=plan)
    task = await app.state.graph.start_bi(
        GraphRequest(
            agent_id=str(agent_id), query=payload.question, bi_request=payload.model_dump(mode="json")
        )
    )
    for _ in range(100):
        snapshot = await app.state.graph.tasks.get(task["task_id"], agent_id)
        if snapshot["status"] in TERMINAL:
            break
        await asyncio.sleep(0.05)
    assert snapshot["status"] == "COMPLETED", snapshot
    assert len(snapshot["result"]["charts"]) == 1
    provenance = snapshot["result"]["result"]["provenance"]
    assert (
        app.state.graph.artifacts.get(task["task_id"], agent_id, provenance["artifactId"])["kind"]
        == "query_result"
    )
    async with app.state.database.session_factory() as session:
        action = await app.state.graph.bi.plan(
            BIRequest(agent_id=agent_id, question="看一下第一辆", conversation_id=task["conversation_id"]),
            session,
            app.state.llm,
        )
    assert action["action"] == "open_dashboard"
    assert action["filters"]["vehicle_id"] == 1
    await app.state.graph.close()


async def test_task_cancel_is_idempotent_and_cross_manager(app):
    task_id = str(uuid4())
    first = app.state.graph.tasks
    second = TaskManager(app.state.database)
    await first.create(task_id, 12, "scope")
    await first.running(task_id, "query", 20)
    await first.partial(task_id, {"evidence": "retained"})
    await second.cancel(task_id, 12)
    assert await first.cancelled(task_id)
    await first.finish(task_id, "COMPLETED")
    await second.cancel(task_id, 12)
    result = await first.get(task_id)
    assert result["status"] == "CANCELLED"
    assert result["result"] == {"evidence": "retained"}
    with pytest.raises(NotFoundError):
        await second.get(task_id, 13)


async def test_running_graph_cancels_without_waiting_for_slow_model(app, scania_files, monkeypatch):
    agent_id = await provision(app, scania_files)
    entered = asyncio.Event()

    async def slow(*args, **kwargs):
        entered.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(app.state.llm, "complete", slow)
    task = await app.state.graph.start_bi(
        GraphRequest(
            agent_id=str(agent_id),
            query="比较 SCANIA 规格",
            bi_request={
                "agent_id": agent_id,
                "question": "比较 SCANIA 规格",
                "dataset_id": "scania_component_x",
            },
        )
    )
    await asyncio.wait_for(entered.wait(), 3)
    await TaskManager(app.state.database).cancel(task["task_id"], agent_id)
    for _ in range(50):
        result = await app.state.graph.tasks.get(task["task_id"])
        if result["status"] == "CANCELLED":
            break
        await asyncio.sleep(0.05)
    assert result["status"] == "CANCELLED"
    await app.state.graph.close()


async def test_api_validation_missing_configuration_and_agent_ownership(app, client, scania_files):
    response = await client.post("/api/bi/datasets/scania_component_x/dashboard", json={"filters": {}})
    assert response.status_code == 503
    agent_id = await provision(app, scania_files)
    response = await client.post(
        "/api/bi/datasets/scania_component_x/dashboard", json={"filters": {"start_date": "2015-01-01"}}
    )
    assert response.status_code == 400
    response = await client.post(
        "/api/bi/datasets/scania_component_x/dashboard", json={"filters": {"split": "validation"}}
    )
    assert response.json()["overview"]["vehicle_count"] == 3
    response = await client.post("/api/bi/plan", json={"agent_id": agent_id, "question": "打开 SCANIA 看板"})
    assert response.json()["action"] == "open_dashboard"
    response = await client.post(
        "/api/bi/plan", json={"agent_id": agent_id, "question": "SCANIA 最近7天故障"}
    )
    assert response.json()["action"] == "clarify"


async def test_planner_cannot_drop_inherited_filters(app, scania_files, monkeypatch):
    agent_id = await provision(app, scania_files)

    async def invalid(*args, **kwargs):
        return PlannerDecision(
            action="analyze", plan=BIPlan(dataset_id="scania_component_x", metric_ids=["vehicle_count"])
        )

    monkeypatch.setattr(app.state.llm, "complete", invalid)
    async with app.state.database.session_factory() as session:
        with pytest.raises(DataAgentError, match="omitted"):
            await app.state.graph.bi.plan(
                BIRequest(
                    agent_id=agent_id,
                    question="车辆数",
                    dataset_id="scania_component_x",
                    filters={"split": "validation"},
                ),
                session,
                app.state.llm,
            )


async def test_known_labels_rank_without_predicting_risk(scania):
    result = await scania.query(
        BIPlan(
            dataset_id=scania.dataset_id,
            intent="ranking",
            metric_ids=["known_label_level"],
            dimension="vehicle_id",
            filters={"split": "validation"},
        )
    )
    assert [row["vehicle_id"] for row in result["data"]] == [1, 3, 2]
    assert [row["known_label_level"] for row in result["data"]] == [4, 2, 0]


async def test_natural_language_dashboard_filter_routing(scania):
    filters = await scania.dashboard_filters(
        "打开 SCANIA 验证集看板，车辆189，time_step 0到100，Spec_0=Cat0", {}
    )
    assert filters["split"] == "validation"
    assert filters["vehicle_id"] == 189
    assert filters["max_time"] == 100
    assert filters["specifications"] == {"Spec_0": "Cat0"}


async def test_original_dashboard_deep_analysis_keeps_legacy_agent(app, scania_files):
    from types import SimpleNamespace

    agent_id = await provision(app, scania_files)
    context = SimpleNamespace(
        agent_id=agent_id,
        state={"_CONVERSATION_ID": "legacy"},
        query="请对工业设备在以下 Dashboard 筛选范围内做深度分析，并使用 SQL/Python 和 RAG。",
    )
    assert not await app.state.graph.bi.should_route(context)
    context.query = "打开工业设备看板，设备1，最近7天"
    assert await app.state.graph.bi.should_route(context)


async def test_duplicate_metadata_rejected_before_join(scania, scania_files):
    path = scania_files / "raw" / "train_tte.csv"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("1,1,25\n")
    with pytest.raises(DataAgentError, match="unique"):
        await scania.dashboard({})


async def wait_task(app, task_id, statuses):
    for _ in range(120):
        result = await app.state.graph.tasks.get(task_id)
        if result["status"] in statuses:
            return result
        await asyncio.sleep(0.05)
    pytest.fail(f"Task did not finish: {result}")


async def test_bi_human_review_resume_and_waiting_cancel(app, client, scania_files):
    agent_id = await provision(app, scania_files)
    async with app.state.database.session_factory() as session:
        agent = await session.get(Agent, agent_id)
        agent.human_review_enabled = 1
        await session.commit()
    payload = {
        "agent_id": agent_id,
        "question": "车辆数",
        "dataset_id": "scania_component_x",
        "plan": {"dataset_id": "scania_component_x", "metric_ids": ["vehicle_count"]},
    }
    created = (await client.post("/api/bi/analyze", json=payload)).json()
    waiting = await wait_task(app, created["task_id"], {"WAITING"} | TERMINAL)
    assert waiting["status"] == "WAITING"
    resumed = await client.post(
        f"/api/bi/tasks/{created['task_id']}/resume", params={"agent_id": agent_id}, json={"approved": True}
    )
    assert resumed.status_code == 202
    # The endpoint dispatches asynchronously, so wait until WAITING has been exited.
    result = await wait_task(app, created["task_id"], TERMINAL)
    assert result["status"] == "COMPLETED", result
    assert result["result"]["result"]["data"][0]["vehicle_count"] == 3
    created = (await client.post("/api/bi/analyze", json=payload)).json()
    await wait_task(app, created["task_id"], {"WAITING"} | TERMINAL)
    stopped = await client.post(f"/api/bi/tasks/{created['task_id']}/cancel", params={"agent_id": agent_id})
    assert stopped.json()["status"] == "CANCELLED"
    resumed = await client.post(
        f"/api/bi/tasks/{created['task_id']}/resume", params={"agent_id": agent_id}, json={"approved": True}
    )
    assert resumed.status_code == 409


async def test_cancellable_sql_joins_worker_before_return(tmp_path):
    from sqlalchemy import create_engine
    from dataagent.query_execution import run_read_operation

    engine = create_engine(f"sqlite:///{tmp_path / 'cancel.db'}")
    sql = (
        "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r WHERE x<100000000) SELECT SUM(x) FROM r"
    )
    started = perf_counter()
    with pytest.raises(TimeoutError):
        await run_read_operation(engine, lambda c: c.exec_driver_sql(sql).scalar(), 0.1)
    assert perf_counter() - started < 3
    assert engine.pool.checkedout() == 0
    assert await run_read_operation(engine, lambda c: c.exec_driver_sql("SELECT 1").scalar(), 2) == 1
    engine.dispose()


async def test_bi_report_reuses_existing_generator(app, scania_files):
    agent_id = await provision(app, scania_files)
    payload = BIRequest(
        agent_id=agent_id,
        question="车辆数报告",
        dataset_id="scania_component_x",
        report=True,
        plan=BIPlan(dataset_id="scania_component_x", metric_ids=["vehicle_count"]),
    )
    created = await app.state.graph.start_bi(
        GraphRequest(
            agent_id=str(agent_id), query=payload.question, bi_request=payload.model_dump(mode="json")
        )
    )
    result = await wait_task(app, created["task_id"], TERMINAL)
    assert result["status"] == "COMPLETED", result["error"]
    run = app.state.graph.artifacts.load(created["task_id"], agent_id)
    assert any(a["kind"] == "report" for a in run["artifacts"])
    assert result["result"]["result"]["data"][0]["vehicle_count"] == 3


async def test_mcp_bi_uses_shared_catalog_and_graph(app, scania_files, monkeypatch):
    from mcp import Client
    import dataagent.mcp_server as module

    agent_id = await provision(app, scania_files)
    monkeypatch.setattr(module, "get_settings", lambda: app.state.settings)
    monkeypatch.setattr(module, "get_database", lambda: app.state.database)
    monkeypatch.setattr(module, "GraphEngine", lambda *args, **kwargs: app.state.graph)
    async with Client(module.create_mcp_server()) as client:
        catalog = await client.call_tool("get_bi_catalog", {"datasetId": "scania_component_x"})
        assert not catalog.is_error
        assert "known_label_level" in str(catalog.content)
        launched = await client.call_tool("analyze_bi", {"agentId": agent_id, "question": "打开 SCANIA 看板"})
        assert not launched.is_error
        payload = launched.structured_content
        if "result" in payload:
            payload = payload["result"]
        result = await wait_task(app, payload["task_id"], TERMINAL)
        assert result["result"]["action"] == "open_dashboard"
        received = await client.call_tool("get_bi_task", {"agentId": agent_id, "taskId": payload["task_id"]})
        assert not received.is_error
        assert "COMPLETED" in str(received.content)
