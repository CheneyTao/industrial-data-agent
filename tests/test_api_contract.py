from __future__ import annotations

from dataagent.app import create_app

EXPECTED_OPERATIONS = {
    tuple(line.split(" ", 1))
    for line in """
POST /api/agent
GET /api/chat-attachments/capabilities
POST /api/chat-attachments
GET /api/chat-attachments/{attachment_id}
POST /api/stream/search
GET /api/bi/datasets
GET /api/bi/datasets/{dataset_id}
GET /api/bi/datasets/{dataset_id}/metrics
POST /api/bi/datasets/{dataset_id}/dashboard
POST /api/bi/datasets/{dataset_id}/entities/{entity_id}
POST /api/bi/plan
POST /api/bi/analyze
GET /api/bi/tasks/{task_id}
POST /api/bi/tasks/{task_id}/cancel
POST /api/bi/tasks/{task_id}/resume
GET /api/bi/tasks/{task_id}/events
POST /api/dashboard/analysis-context
GET /api/dashboard/error-ranking
GET /api/dashboard/failure-by-model
GET /api/dashboard/failure-trend
GET /api/dashboard/failure-types
GET /api/dashboard/filters
GET /api/dashboard/index-audit
GET /api/dashboard/machine-ranking
GET /api/dashboard/machine/{machine_id}/telemetry
GET /api/dashboard/maintenance-analysis
GET /api/dashboard/overview
GET /api/analysis/runs/{run_id}
GET /api/analysis/runs/{run_id}/artifacts/{artifact_id}
GET /api/agent-knowledge/agent/{agent_id}
POST /api/agent-knowledge/create
POST /api/agent-knowledge/query/page
PUT /api/agent-knowledge/recall/{item_id}
POST /api/agent-knowledge/retry-embedding/{item_id}
GET /api/agent-knowledge/statistics/{agent_id}
DELETE /api/agent-knowledge/{item_id}
GET /api/agent-knowledge/{item_id}
PUT /api/agent-knowledge/{item_id}
GET /api/agent/list
DELETE /api/agent/{agent_id}
GET /api/agent/{agent_id}
PUT /api/agent/{agent_id}
DELETE /api/agent/{agent_id}/api-key
GET /api/agent/{agent_id}/api-key
POST /api/agent/{agent_id}/api-key/enable
POST /api/agent/{agent_id}/api-key/generate
POST /api/agent/{agent_id}/api-key/reset
GET /api/agent/{agent_id}/datasources
GET /api/agent/{agent_id}/datasources/active
POST /api/agent/{agent_id}/datasources/init
POST /api/agent/{agent_id}/datasources/tables
PUT /api/agent/{agent_id}/datasources/toggle
DELETE /api/agent/{agent_id}/datasources/{datasource_id}
POST /api/agent/{agent_id}/datasources/{datasource_id}
POST /api/agent/{agent_id}/offline
GET /api/agent/{agent_id}/preset-questions
POST /api/agent/{agent_id}/preset-questions
DELETE /api/agent/{agent_id}/preset-questions/{question_id}
POST /api/agent/{agent_id}/publish
DELETE /api/agent/{agent_id}/sessions
GET /api/agent/{agent_id}/sessions
POST /api/agent/{agent_id}/sessions
GET /api/agent/{agent_id}/sessions/stream
GET /api/business-knowledge
POST /api/business-knowledge
POST /api/business-knowledge/recall/{item_id}
POST /api/business-knowledge/refresh-vector-store
POST /api/business-knowledge/retry-embedding/{item_id}
DELETE /api/business-knowledge/{item_id}
GET /api/business-knowledge/{item_id}
PUT /api/business-knowledge/{item_id}
GET /api/datasource
POST /api/datasource
GET /api/datasource/types
DELETE /api/datasource/{source_id}
GET /api/datasource/{source_id}
PUT /api/datasource/{source_id}
GET /api/datasource/{source_id}/logical-relations
POST /api/datasource/{source_id}/logical-relations
PUT /api/datasource/{source_id}/logical-relations
DELETE /api/datasource/{source_id}/logical-relations/{relation_id}
PUT /api/datasource/{source_id}/logical-relations/{relation_id}
GET /api/datasource/{source_id}/tables
GET /api/datasource/{source_id}/tables/{table_name}/columns
POST /api/datasource/{source_id}/test
POST /api/model-config/activate/{model_id}
POST /api/model-config/add
GET /api/model-config/check-ready
GET /api/model-config/list
POST /api/model-config/test/{model_id}
PUT /api/model-config/update
DELETE /api/model-config/{model_id}
GET /api/prompt-config/active-all/{prompt_type}
GET /api/prompt-config/active/{prompt_type}
POST /api/prompt-config/batch-disable
POST /api/prompt-config/batch-enable
GET /api/prompt-config/list
GET /api/prompt-config/list-by-type/{prompt_type}
POST /api/prompt-config/save
GET /api/prompt-config/types
DELETE /api/prompt-config/{config_id}
GET /api/prompt-config/{config_id}
POST /api/prompt-config/{config_id}/disable
POST /api/prompt-config/{config_id}/display-order
POST /api/prompt-config/{config_id}/enable
POST /api/prompt-config/{config_id}/priority
GET /api/semantic-model
POST /api/semantic-model
DELETE /api/semantic-model/batch
POST /api/semantic-model/batch-import
PUT /api/semantic-model/disable
PUT /api/semantic-model/enable
POST /api/semantic-model/import/excel
GET /api/semantic-model/template/download
DELETE /api/semantic-model/{item_id}
GET /api/semantic-model/{item_id}
PUT /api/semantic-model/{item_id}
DELETE /api/sessions/{session_id}
GET /api/sessions/{session_id}/messages
POST /api/sessions/{session_id}/messages
PUT /api/sessions/{session_id}/pin
PUT /api/sessions/{session_id}/rename
POST /api/sessions/{session_id}/reports/html
GET /api/stream/search
POST /api/stream/stop
POST /api/upload/avatar
GET /api/upload/{file_path}
GET /echo/ok
GET /health
""".strip().splitlines()
}

PYTHON_COMPATIBILITY_EXTENSIONS = {
    ("GET", "/api/chat-attachments/capabilities"),
    ("POST", "/api/chat-attachments"),
    ("GET", "/api/chat-attachments/{attachment_id}"),
    ("POST", "/api/stream/search"),
    ("GET", "/api/analysis/runs/{run_id}"),
    ("GET", "/api/analysis/runs/{run_id}/artifacts/{artifact_id}"),
    ("GET", "/api/agent-knowledge/agent/{agent_id}"),
    ("GET", "/api/agent-knowledge/statistics/{agent_id}"),
    ("GET", "/health"),
}

REPORT_SYSTEM_OPERATIONS = {
    ("POST", "/api/diagnostics"),
    ("GET", "/api/report-operations/status"),
    ("POST", "/api/report-operations/schedules"),
    ("GET", "/api/report-operations/schedules"),
    ("POST", "/api/report-operations/schedules/{schedule_id}/pause"),
    ("POST", "/api/report-operations/schedules/{schedule_id}/trigger"),
    ("GET", "/api/report-operations/runs"),
    ("GET", "/api/report-operations/runs/{run_id}"),
    ("POST", "/api/report-operations/runs/{run_id}/refresh"),
    ("POST", "/api/report-operations/actions"),
    ("GET", "/api/report-operations/actions"),
    ("POST", "/api/report-operations/actions/{action_id}/review"),
}
EXPECTED_OPERATIONS |= REPORT_SYSTEM_OPERATIONS
PYTHON_COMPATIBILITY_EXTENSIONS |= REPORT_SYSTEM_OPERATIONS


def test_openapi_operation_contract_is_complete() -> None:
    schema = create_app().openapi()
    actual = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual == EXPECTED_OPERATIONS
    assert len(actual - PYTHON_COMPATIBILITY_EXTENSIONS) == 120


def test_graph_sse_contract_is_explicit() -> None:
    operation = create_app().openapi()["paths"]["/api/stream/search"]["get"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    names = {item["name"] for item in operation["parameters"]}
    assert {
        "agentId",
        "conversationId",
        "threadId",
        "query",
        "humanFeedback",
        "humanFeedbackContent",
        "rejectedPlan",
        "nl2sqlOnly",
        "X-API-Key",
        "authorization",
    } <= names
