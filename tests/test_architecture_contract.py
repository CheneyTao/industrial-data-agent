from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from pathlib import Path

from dataagent.connectors import SUPPORTED_DATASOURCES
from dataagent.models import Base
from dataagent.prompts import load_prompt, render_prompt
from dataagent.workflow.nodes import EvidenceRewriteOutput, FeasibilityOutput, build_nodes
from dataagent.workflow.state import (
    COLUMN_DOCUMENTS_FOR_SCHEMA_OUTPUT,
    DATASOURCE_ID,
    DISPATCHER_NAMES,
    INPUT_KEY,
    NL2SQL_GRAPH_NAME,
    NODE_ORDER,
    PROJECT_PROPERTIES_PREFIX,
    SCHEMA_RECALL_NODE_OUTPUT,
    STATE_KEYS,
    STREAM_EVENT_COMPLETE,
    STREAM_EVENT_ERROR,
)

ROOT = Path(__file__).resolve().parents[1]

PROMPT_HASHES = {
    "agent-knowledge.txt": "96bc138ba99fdfdd37bd0cb46b1e5871fca7ab5b93ef17bea35c6b3490c71f0c",
    "business-knowledge.txt": "a48524d415f74afe63151c3e6d1bad38f4f5f0001a5fbfb702326e2ec9faaa0d",
    "data-view-analyze.txt": "bae3004a0ed448fad9bb5302d8fb0a63f7f5086893cda088caa20a153538b450",
    "evidence-query-rewrite.txt": "8d977183ae5074085ef5cb310791da9b141f17a15d40297d7d2066ae333dcd7c",
    "feasibility-assessment.txt": "371e7592fe6d5d1f415c74f25ce71e1e5de194f9ffb1e54166e55de5040f4685",
    "intent-recognition.txt": "db16252a1e2063398729ecd4c1a9ea9305dddf2a88b1fdc23f2d3536e016fdcd",
    "json-fix.txt": "82777a849f0aade6eebad6a235560c222ddd122ebb58c947239f33d0952b91d8",
    "mix-selector.txt": "350c4e131f7f93a187f75b549f7c58e9e299ef70309805f34b81c7c35dfebb5e",
    "new-sql-generate.txt": "1c372828c52fb88ad465d464328ff708f5798cb338a752d106cfa51aeee612cd",
    "planner.txt": "e1d0e09ee46599ab5a3d03c66fab7696b6bb96b80f5685d71933aceb26c1e2fa",
    "python-analyze.txt": "42d79aea07755497f1c61a3ee81a1cfddfd4c0b595ac7b4b0693016f942f0722",
    "python-generator.txt": "ccb199e17eb7750683103a7cc720277d16daa3222e4ac51ca13781e25e667c11",
    "query-enhancement.txt": "d56cb36f2080e3a2fc9bf25c5641f59b861e7d7a1acbda04fb107a2f6cdd56a0",
    "report-generator-plain.txt": "8f72729fb7a146537fe4ae23db7c2e44453a4ded8e965b6e56af755150fc05a7",
    "semantic-consistency.txt": "fda7ed6ac69b08b8352f2ab305a0708a11c207c995a05e500075238faa6bd760",
    "semantic-model.txt": "335cb469c19990226ad57e08c9bf9adb7e17721746929cc315f9fad94376ac8a",
    "sql-error-fixer.txt": "6abad45d450c98699238b20a52af0cd99aa0ac871e639ca5627a321cce7e8177",
}

JAVA_PACKAGE_COUNTS = {
    "annotation": 3,
    "aop": 1,
    "bo": 9,
    "config": 7,
    "connector": 34,
    "constant": 2,
    "controller": 15,
    "converter": 3,
    "dto": 33,
    "entity": 12,
    "enums": 10,
    "event": 3,
    "exception": 2,
    "mapper": 13,
    "prompt": 3,
    "properties": 4,
    "root": 1,
    "security": 4,
    "service": 84,
    "splitter": 3,
    "strategy": 1,
    "util": 17,
    "vo": 12,
    "workflow": 27,
}


def test_java_to_python_inventory_covers_all_303_production_files() -> None:
    with (ROOT / "docs" / "JAVA_TO_PYTHON_FILE_MAP.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 303
    assert len({row["java_path"] for row in rows}) == 303
    assert all(row["coverage"] == "implemented" for row in rows)
    counts = Counter(row["java_package"].split(".", 1)[0] for row in rows)
    assert counts == JAVA_PACKAGE_COUNTS
    for row in rows:
        targets = row["python_targets"].split(";")
        assert targets and all((ROOT / target).exists() for target in targets), row


def test_graph_state_node_and_dispatcher_inventory_matches_java() -> None:
    expected_state_keys = {
        "input",
        "agentId",
        "MULTI_TURN_CONTEXT",
        "INTENT_RECOGNITION_NODE_OUTPUT",
        "QUERY_ENHANCE_NODE_OUTPUT",
        "GENEGRATED_SEMANTIC_MODEL_PROMPT",
        "EVIDENCE",
        "TABLE_DOCUMENTS_FOR_SCHEMA",
        "COLUMN_DOCUMENTS__FOR_SCHEMA_OUTPUT",
        "TABLE_RELATION_OUTPUT",
        "TABLE_RELATION_EXCEPTION_OUTPUT",
        "TABLE_RELATION_RETRY_COUNT",
        "DB_DIALECT_TYPE",
        "FEASIBILITY_ASSESSMENT_NODE_OUTPUT",
        "SQL_GENERATE_SCHEMA_MISSING_ADVICE",
        "SQL_GENERATE_OUTPUT",
        "SQL_GENERATE_COUNT",
        "SQL_REGENERATE_REASON",
        "SEMANTIC_CONSISTENCY_NODE_OUTPUT",
        "PLANNER_NODE_OUTPUT",
        "PLAN_CURRENT_STEP",
        "PLAN_NEXT_NODE",
        "PLAN_VALIDATION_STATUS",
        "PLAN_VALIDATION_ERROR",
        "PLAN_REPAIR_COUNT",
        "SQL_EXECUTE_NODE_OUTPUT",
        "SQL_RESULT_LIST_MEMORY",
        "PYTHON_IS_SUCCESS",
        "PYTHON_TRIES_COUNT",
        "PYTHON_FALLBACK_MODE",
        "PYTHON_EXECUTE_NODE_OUTPUT",
        "PYTHON_GENERATE_NODE_OUTPUT",
        "PYTHON_ANALYSIS_NODE_OUTPUT",
        "IS_ONLY_NL2SQL",
        "HUMAN_REVIEW_ENABLED",
        "HUMAN_FEEDBACK_DATA",
        "TRACE_THREAD_ID",
        "result",
        "final_answer",
    }
    assert set(STATE_KEYS) == expected_state_keys
    assert len(STATE_KEYS) == 39
    assert list(build_nodes()) == NODE_ORDER
    assert len(NODE_ORDER) == 16
    assert len(DISPATCHER_NAMES) == 11
    assert len(set(DISPATCHER_NAMES)) == 11
    assert (PROJECT_PROPERTIES_PREFIX, INPUT_KEY, DATASOURCE_ID) == (
        "spring.ai.alibaba.data-agent",
        "input",
        "datasourceId",
    )
    assert NL2SQL_GRAPH_NAME == "nl2sqlGraph"
    assert SCHEMA_RECALL_NODE_OUTPUT == "SCHEMA_RECALL_NODE_OUTPUT"
    assert COLUMN_DOCUMENTS_FOR_SCHEMA_OUTPUT == "COLUMN_DOCUMENTS__FOR_SCHEMA_OUTPUT"
    assert (STREAM_EVENT_COMPLETE, STREAM_EVENT_ERROR) == ("complete", "error")


def test_management_schema_preserves_upstream_and_adds_versioned_report_tables() -> None:
    upstream = {
        "agent",
        "datasource",
        "agent_datasource",
        "agent_datasource_tables",
        "logical_relation",
        "semantic_model",
        "business_knowledge",
        "agent_knowledge",
        "agent_preset_question",
        "chat_session",
        "chat_message",
        "user_prompt_config",
        "model_config",
    }
    assert set(Base.metadata.tables) == upstream | {
        "graph_checkpoint",
        "conversation_turn",
        "analysis_task",
        "bi_analysis_context",
        "report_schedule_v1",
        "report_run_v1",
        "report_action_v1",
    }


def test_all_seventeen_reviewed_prompts_are_byte_locked_and_renderable() -> None:
    prompt_dir = ROOT / "src" / "dataagent" / "prompts"
    actual = {path.name for path in prompt_dir.glob("*.txt")}
    assert actual == set(PROMPT_HASHES)
    variable_pattern = re.compile(r"(?<!\\)\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for filename, expected_hash in PROMPT_HASHES.items():
        raw = load_prompt(filename)
        assert hashlib.sha256((prompt_dir / filename).read_bytes()).hexdigest() == expected_hash
        variables = set(variable_pattern.findall(raw))
        rendered = render_prompt(filename, **{name: "TEST_VALUE" for name in variables})
        assert not variable_pattern.search(rendered), filename


def test_structured_prompt_models_use_the_original_wire_names() -> None:
    rewrite = EvidenceRewriteOutput.model_validate({"standalone_query": "分析上月收入"})
    assert rewrite.standalone_query == "分析上月收入"
    feasibility = FeasibilityOutput.model_validate(
        {"requirementType": "NEED_CLARIFICATION", "language": "zh-CN", "content": "请补充月份"}
    )
    assert feasibility.requirement_type == "NEED_CLARIFICATION"
    assert feasibility.model_dump(by_alias=True)["requirementType"] == "NEED_CLARIFICATION"


def test_all_seven_java_database_handlers_are_publicly_available() -> None:
    assert [item["code"] for item in SUPPORTED_DATASOURCES] == [
        "mysql",
        "postgresql",
        "h2",
        "dameng",
        "sqlserver",
        "oracle",
        "hive",
    ]
