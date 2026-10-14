from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import select

from dataagent.models import (
    Agent,
    AgentDatasource,
    AgentDatasourceTable,
    ConversationTurn,
    Datasource,
    GraphCheckpoint,
)
from dataagent.sandbox import SandboxResult
from dataagent.schemas import GraphEventType, GraphRequest, Plan, TextType
from dataagent.workflow.nodes import SemanticOutput, _schema_ddl
from dataagent.workflow.state import NODE_ORDER


async def _provision(app: FastAPI, path: Path, *, human_review: bool = False) -> int:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, amount REAL NOT NULL)")
        connection.executemany("INSERT INTO sales(amount) VALUES (?)", [(11,), (22,), (33,)])
    async with app.state.database.session_factory() as session:
        agent = Agent(
            name="Workflow Agent",
            status="published",
            prompt="State all monetary units explicitly.",
            human_review_enabled=int(human_review),
        )
        source = Datasource(
            name="Workflow source",
            type="sqlite",
            host="localhost",
            port=0,
            database_name=str(path),
            username="",
            password="",
            connection_url=f"sqlite:///{path.as_posix()}",
            status="active",
        )
        session.add_all([agent, source])
        await session.flush()
        association = AgentDatasource(
            agent_id=agent.id,
            datasource_id=source.id,
            is_active=1,
        )
        session.add(association)
        await session.flush()
        session.add(AgentDatasourceTable(agent_datasource_id=association.id, table_name="sales"))
        await session.commit()
        await app.state.schema_indexer.initialize(session, agent.id)
        return agent.id


async def _events(app: FastAPI, request: GraphRequest):
    return [item async for item in app.state.graph.stream(request)]


def test_all_sixteen_upstream_nodes_are_registered(app: FastAPI) -> None:
    assert list(app.state.graph.nodes) == NODE_ORDER + [
        "BI_ROUTE_NODE", "BI_EXECUTE_NODE", "BI_INTERPRET_NODE"
    ]
    assert len(app.state.graph.nodes) == 19


async def test_full_mock_text_to_sql_execution_and_report(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "workflow.db")
    events = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="conversation-full",
            query="查询全部销售额并生成报告",
        ),
    )
    payloads = [event for _, event in events]
    names = [event.node_name for event in payloads if event.node_name]
    expected_path = [
        "INTENT_RECOGNITION_NODE",
        "EVIDENCE_RECALL_NODE",
        "QUERY_ENHANCE_NODE",
        "SCHEMA_RECALL_NODE",
        "TABLE_RELATION_NODE",
        "FEASIBILITY_ASSESSMENT_NODE",
        "PLANNER_NODE",
        "PLAN_EXECUTOR_NODE",
        "SQL_GENERATE_NODE",
        "SEMANTIC_CONSISTENCY_NODE",
        "SQL_EXECUTE_NODE",
        "PLAN_EXECUTOR_NODE",
        "REPORT_GENERATOR_NODE",
    ]
    cursor = 0
    for name in names:
        if cursor < len(expected_path) and name == expected_path[cursor]:
            cursor += 1
    assert cursor == len(expected_path), names

    sql_events = [event for event in payloads if event.text_type == TextType.SQL]
    assert any("SELECT" in event.text.upper() for event in sql_events)
    result_events = [event for event in payloads if event.text_type == TextType.RESULT_SET]
    assert result_events
    table_payload = json.loads(result_events[0].text)
    assert table_payload["resultSet"]["rowCount"] == 3
    final = [event for event in payloads if event.event_type == GraphEventType.FINAL_ANSWER]
    assert final and "数据分析报告" in final[-1].text
    assert events[-1][0] == "complete" and events[-1][1].complete

    async with app.state.database.session_factory() as session:
        turns = (
            await session.execute(
                select(ConversationTurn).where(ConversationTurn.conversation_id == "conversation-full")
            )
        ).scalars()
        assert len(list(turns)) == 1


async def test_explicit_table_name_survives_vector_threshold(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "explicit-table.db")
    app.state.settings.vector_table_threshold = 1.1
    events = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="conversation-explicit-table",
            query="请查询 sales 表全部数据并生成报告",
        ),
    )
    payloads = [event for _, event in events]
    assert any(event.node_name == "SQL_EXECUTE_NODE" for event in payloads)
    assert not any("未检索到相关数据表" in event.text for event in payloads)


async def test_nl2sql_only_returns_sql_without_executing_report(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "nl2sql.db")
    sql = await app.state.graph.nl2sql("列出 sales", str(agent_id))
    assert sql.upper().startswith("SELECT")
    assert "SALES" in sql.upper()


async def test_human_review_checkpoint_resume_and_cleanup(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "hitl.db", human_review=True)
    initial = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="conversation-hitl",
            query="分析 sales",
        ),
    )
    feedback_events = [
        event for _, event in initial if event.event_type == GraphEventType.HUMAN_FEEDBACK_REQUIRED
    ]
    assert len(feedback_events) == 1
    thread_id = feedback_events[0].thread_id

    async with app.state.database.session_factory() as session:
        checkpoint = await session.get(GraphCheckpoint, thread_id)
        assert checkpoint is not None
        assert checkpoint.next_node == "HUMAN_FEEDBACK_NODE"

    resumed = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="conversation-hitl",
            thread_id=thread_id,
            query="分析 sales",
            human_feedback_content="批准执行",
        ),
    )
    assert any(event.text_type == TextType.RESULT_SET for _, event in resumed)
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for _, event in resumed)
    async with app.state.database.session_factory() as session:
        assert await session.get(GraphCheckpoint, thread_id) is None


async def test_wrong_conversation_cannot_read_or_delete_hitl_checkpoint(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "hitl-ownership.db", human_review=True)
    initial = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="checkpoint-owner",
            query="分析 sales",
        ),
    )
    thread_id = next(
        event.thread_id for _, event in initial if event.event_type == GraphEventType.HUMAN_FEEDBACK_REQUIRED
    )

    rejected = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="wrong-conversation",
            thread_id=thread_id,
            query="分析 sales",
            human_feedback_content="批准执行",
        ),
    )
    assert any(event.error and "does not belong" in event.text for _, event in rejected)
    assert (await app.state.graph.tasks.get(thread_id, agent_id))["status"] == "WAITING"
    async with app.state.database.session_factory() as session:
        assert await session.get(GraphCheckpoint, thread_id) is not None

    resumed = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="checkpoint-owner",
            thread_id=thread_id,
            query="分析 sales",
            human_feedback_content="批准执行",
        ),
    )
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for _, event in resumed)


async def test_rejected_plan_replans_and_pauses_again(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "reject.db", human_review=True)
    first = await _events(
        app,
        GraphRequest(agent_id=str(agent_id), conversation_id="conversation-reject", query="分析 sales"),
    )
    thread_id = next(
        event.thread_id for _, event in first if event.event_type == GraphEventType.HUMAN_FEEDBACK_REQUIRED
    )
    async with app.state.database.session_factory() as session:
        original_turn_id = (
            await session.execute(
                select(ConversationTurn.id).where(ConversationTurn.conversation_id == "conversation-reject")
            )
        ).scalar_one()
        # Keep a larger row id alive so SQLite cannot reuse the deleted turn's
        # integer key; this makes replacement observable without implementation mocks.
        session.add(
            ConversationTurn(
                conversation_id="replacement-id-sentinel",
                question="sentinel",
                planner_output="sentinel",
            )
        )
        await session.commit()
    rejected = await _events(
        app,
        GraphRequest(
            agent_id=str(agent_id),
            conversation_id="conversation-reject",
            thread_id=thread_id,
            query="分析 sales",
            human_feedback_content="请按地区拆分",
            rejected_plan=True,
        ),
    )
    assert sum(event.event_type == GraphEventType.HUMAN_FEEDBACK_REQUIRED for _, event in rejected) == 1
    async with app.state.database.session_factory() as session:
        checkpoint = await session.get(GraphCheckpoint, thread_id)
        assert checkpoint is not None
        state = json.loads(checkpoint.state_json)
        assert state["PLAN_REPAIR_COUNT"] == 1
        turns = (
            (
                await session.execute(
                    select(ConversationTurn).where(ConversationTurn.conversation_id == "conversation-reject")
                )
            )
            .scalars()
            .all()
        )
        assert len(turns) == 1
        assert turns[0].id != original_turn_id


async def test_stop_clears_inactive_hitl_checkpoint_by_thread_or_conversation(
    app: FastAPI, tmp_path: Path
) -> None:
    agent_id = await _provision(app, tmp_path / "stop-hitl.db", human_review=True)

    async def paused_run(conversation_id: str) -> str:
        events = await _events(
            app,
            GraphRequest(
                agent_id=str(agent_id),
                conversation_id=conversation_id,
                query="分析 sales",
            ),
        )
        return next(
            event.thread_id
            for _, event in events
            if event.event_type == GraphEventType.HUMAN_FEEDBACK_REQUIRED
        )

    first_thread = await paused_run("stop-by-thread")
    await app.state.graph.stop(thread_id=first_thread, conversation_id="stop-by-thread")
    async with app.state.database.session_factory() as session:
        assert await session.get(GraphCheckpoint, first_thread) is None

    second_thread = await paused_run("stop-by-conversation")
    await app.state.graph.stop(thread_id=None, conversation_id="stop-by-conversation")
    async with app.state.database.session_factory() as session:
        assert await session.get(GraphCheckpoint, second_thread) is None


def test_schema_prompt_preserves_samples_ranges_mappings_and_relations() -> None:
    ddl = _schema_ddl(
        {
            "table": [
                {
                    "name": "orders",
                    "primaryKeys": ["id"],
                    "column": [
                        {
                            "name": "id",
                            "type": "INTEGER",
                            "description": "订单编号",
                            "data": [1, 2],
                            "range": "1..999",
                            "mapping": {"1": "有效"},
                        }
                    ],
                }
            ],
            "foreignKeys": ["orders.user_id -> users.id"],
        }
    )
    assert "id INTEGER PRIMARY KEY" in ddl
    assert "订单编号" in ddl
    assert "samples=[1, 2]" in ddl
    assert "range=1..999" in ddl
    assert 'mapping={"1": "有效"}' in ddl
    assert "orders.user_id -> users.id" in ddl


async def test_sql_execution_failure_is_fed_back_and_regenerated(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "sql-retry.db")
    original = app.state.llm.complete
    sql_prompts: list[str] = []

    async def complete(session, *, system, user, purpose="general", json_model=None, temperature=None):
        if purpose == "sql-generate":
            sql_prompts.append(user)
            return "SELECT missing_column FROM sales" if len(sql_prompts) == 1 else "SELECT amount FROM sales"
        return await original(
            session,
            system=system,
            user=user,
            purpose=purpose,
            json_model=json_model,
            temperature=temperature,
        )

    app.state.llm.complete = complete
    events = await _events(
        app,
        GraphRequest(agent_id=str(agent_id), conversation_id="sql-retry", query="查询销售额"),
    )
    payloads = [event for _, event in events]

    sql_events = [event.text for event in payloads if event.text_type == TextType.SQL]
    assert sql_events.count("SELECT missing_column FROM sales") == 1
    assert sql_events.count("SELECT amount FROM sales") == 1
    assert any(event.node_name == "SQL_EXECUTE_NODE" and event.error for event in payloads)
    assert "missing_column" in sql_prompts[1]
    assert "no such column" in sql_prompts[1].lower()
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for event in payloads)


class _SequencedSandbox:
    def __init__(self, results: list[SandboxResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str]] = []

    async def execute(self, code: str, input_text: str) -> SandboxResult:
        self.calls.append((code, input_text))
        return self.results.pop(0)


async def test_python_failure_retry_prompt_and_successful_analysis(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "python-retry.db")
    original = app.state.llm.complete
    planner_systems: list[str] = []
    planner_tasks: list[str] = []
    python_calls: list[tuple[str, str]] = []

    plan = Plan.model_validate(
        {
            "thought_process": "Query then calculate.",
            "execution_plan": [
                {
                    "step": 1,
                    "tool_to_use": "SQL_GENERATE_NODE",
                    "tool_parameters": {"instruction": "Select sales amount from sales."},
                },
                {
                    "step": 2,
                    "tool_to_use": "PYTHON_GENERATE_NODE",
                    "tool_parameters": {"instruction": "Sum the prior amount values."},
                },
                {
                    "step": 3,
                    "tool_to_use": "REPORT_GENERATOR_NODE",
                    "tool_parameters": {"summary_and_recommendations": "Report the sum."},
                },
            ],
        }
    )

    async def complete(session, *, system, user, purpose="general", json_model=None, temperature=None):
        if purpose == "planner":
            planner_systems.append(system)
            planner_tasks.append(user)
            return plan
        if purpose == "python-generate":
            python_calls.append((system, user))
            return "raise RuntimeError('first')" if len(python_calls) == 1 else "print('{\"sum\": 66}')"
        return await original(
            session,
            system=system,
            user=user,
            purpose=purpose,
            json_model=json_model,
            temperature=temperature,
        )

    app.state.llm.complete = complete
    sandbox = _SequencedSandbox(
        [
            SandboxResult(False, stderr="boom", error_type="RuntimeError", error_message="boom"),
            SandboxResult(True, stdout='{"sum": 66}'),
        ]
    )
    app.state.graph.sandbox = sandbox
    events = await _events(
        app,
        GraphRequest(agent_id=str(agent_id), conversation_id="python-retry", query="计算销售额总和"),
    )
    payloads = [event for _, event in events]

    assert len(sandbox.calls) == 2, [(name, event.text) for name, event in events if event.error]
    assert len(python_calls) == 2
    assert "500" in python_calls[0][0]
    assert "60s" in python_calls[0][0]
    assert "未指定库时才优先使用标准库" in python_calls[0][0]
    assert "后续分支的别名不会改变最终结果列名" in python_calls[0][0]
    assert "raise RuntimeError('first')" in python_calls[1][1]
    assert "boom" in python_calls[1][1]
    assert "State all monetary units explicitly." in planner_systems[0]
    assert "总计单独返回，绘图结果只含同一粒度的类别" in planner_tasks[0]
    generator_attempts = [event.attempt for event in payloads if event.node_name == "PYTHON_GENERATE_NODE"]
    assert set(generator_attempts) == {1, 2}
    assert any('"sum": 66' in event.text for event in payloads)
    assert any(event.node_name == "PYTHON_ANALYZE_NODE" for event in payloads)
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for event in payloads)


async def test_python_retry_exhaustion_degrades_to_report_instead_of_crashing(
    app: FastAPI, tmp_path: Path
) -> None:
    app.state.settings.python_max_tries_count = 2
    agent_id = await _provision(app, tmp_path / "python-fallback.db")
    original = app.state.llm.complete
    plan = Plan.model_validate(
        {
            "thought_process": "Advanced calculation.",
            "execution_plan": [
                {
                    "step": 1,
                    "tool_to_use": "SQL_GENERATE_NODE",
                    "tool_parameters": {"instruction": "Read id and amount from sales."},
                },
                {
                    "step": 2,
                    "tool_to_use": "PYTHON_GENERATE_NODE",
                    "tool_parameters": {"instruction": "Calculate a statistic."},
                },
                {
                    "step": 3,
                    "tool_to_use": "REPORT_GENERATOR_NODE",
                    "tool_parameters": {"summary_and_recommendations": "Explain availability."},
                },
            ],
        }
    )

    async def complete(session, *, system, user, purpose="general", json_model=None, temperature=None):
        if purpose == "planner":
            return plan
        if purpose == "python-generate":
            return "raise RuntimeError('always')"
        return await original(
            session,
            system=system,
            user=user,
            purpose=purpose,
            json_model=json_model,
            temperature=temperature,
        )

    app.state.llm.complete = complete
    app.state.graph.sandbox = _SequencedSandbox(
        [
            SandboxResult(False, error_type="RuntimeError", error_message="always"),
            SandboxResult(False, error_type="RuntimeError", error_message="always"),
        ]
    )
    events = await _events(
        app,
        GraphRequest(agent_id=str(agent_id), conversation_id="python-fallback", query="高级分析"),
    )
    payloads = [event for _, event in events]
    assert sum(event.node_name == "PYTHON_EXECUTE_NODE" and event.error for event in payloads) == 2
    assert any("Python 高级分析功能暂时不可用" in event.text for event in payloads)
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for event in payloads)
    assert not any(name == "error" for name, _ in events)


async def test_invalid_plan_is_repaired_with_validation_feedback(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "plan-repair.db")
    original = app.state.llm.complete
    planner_prompts: list[str] = []

    async def complete(session, *, system, user, purpose="general", json_model=None, temperature=None):
        if purpose == "planner":
            planner_prompts.append(user)
            if len(planner_prompts) == 1:
                return Plan.model_validate(
                    {
                        "thought_process": "bad",
                        "execution_plan": [
                            {
                                "step": 1,
                                "tool_to_use": "UNSAFE_TOOL",
                                "tool_parameters": {"instruction": "bad"},
                            }
                        ],
                    }
                )
            return Plan.model_validate(
                {
                    "thought_process": "fixed",
                    "execution_plan": [
                        {
                            "step": 1,
                            "tool_to_use": "SQL_GENERATE_NODE",
                            "tool_parameters": {"instruction": "Select all sales rows."},
                        },
                        {
                            "step": 2,
                            "tool_to_use": "REPORT_GENERATOR_NODE",
                            "tool_parameters": {"summary_and_recommendations": "Summarize rows."},
                        },
                    ],
                }
            )
        return await original(
            session,
            system=system,
            user=user,
            purpose=purpose,
            json_model=json_model,
            temperature=temperature,
        )

    app.state.llm.complete = complete
    events = await _events(
        app,
        GraphRequest(agent_id=str(agent_id), conversation_id="plan-repair", query="分析 sales"),
    )
    assert len(planner_prompts) == 2
    assert "UNSAFE_TOOL" in planner_prompts[1]
    assert any(event.node_name == "PLAN_EXECUTOR_NODE" and event.error for _, event in events)
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for _, event in events)


async def test_missing_requirement_replans_before_any_query(app: FastAPI, tmp_path: Path) -> None:
    agent_id = await _provision(app, tmp_path / "coverage.db")
    original = app.state.llm.complete
    planner_prompts, reviews = [], []

    async def complete(session, *, system, user, purpose="general", json_model=None, temperature=None):
        if purpose == "planner":
            planner_prompts.append(user)
        if purpose == "plan-review":
            reviews.append(user)
            return SemanticOutput(passed=len(reviews) > 1, reason="Required period comparison omitted")
        return await original(
            session, system=system, user=user, purpose=purpose, json_model=json_model, temperature=temperature
        )

    app.state.llm.complete = complete
    events = await _events(app, GraphRequest(agent_id=str(agent_id), query="Compare sales by period"))
    assert len(planner_prompts) == len(reviews) == 2
    assert all("allowedPlanSchema" in review and "automaticProvenance" in review for review in reviews)
    assert "Required period comparison omitted" in planner_prompts[1]
    run_id = events[0][1].thread_id
    manifest = app.state.graph.artifacts.load(run_id, agent_id)
    assert sum(item["kind"] == "query_result" for item in manifest["artifacts"]) == 1
    reviews = [item for item in manifest["artifacts"] if item["kind"] == "plan_review"]
    assert len(reviews) == 2
    assert any(event.event_type == GraphEventType.FINAL_ANSWER for _, event in events)
