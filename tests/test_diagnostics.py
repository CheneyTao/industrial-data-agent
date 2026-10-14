from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from dataagent.api.diagnostics import router
from dataagent.artifacts import ArtifactStore
from dataagent.diagnostics import DiagnosisRequest, DiagnosticsService
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.models import Agent
from dataagent.security import hash_api_key


def binary_rows():
    return [
        {
            "device": i,
            "exposed": int(i < 6),
            "failed": int(i < 4 or i == 6),
            "observed": "2026-10-10T10:00:00+08:00",
            "exposure_time": "2026-10-09T10:00:00+08:00",
            "outcome_time": "2026-10-10T09:00:00+08:00",
        }
        for i in range(12)
    ]


def saved_result(settings, rows=None, **overrides):
    store = ArtifactStore(settings)
    run = store.create(1, "Check failure association")
    run_id = run["runId"]
    sql = store.add(run_id, 1, "sql", {"sql": "SELECT * FROM events", "sourceId": "test"})
    rows = binary_rows() if rows is None else rows
    payload = {
        "column": list(binary_rows()[0]),
        "data": rows,
        "rowCount": len(rows),
        "truncated": False,
        "errorMsg": None,
        **overrides,
    }
    result = store.add(run_id, 1, "query_result", payload, [sql["artifactId"]])
    store.step(
        run_id,
        1,
        "execute_query",
        status="succeeded",
        duration_ms=1,
        inputs=[sql["artifactId"]],
        outputs=[result["artifactId"]],
    )
    request = DiagnosisRequest(
        agent_id=1,
        run_id=run_id,
        result_artifact_id=result["artifactId"],
        outcome_field="failed",
        exposure_field="exposed",
        entity_field="device",
        key_fields=["device"],
    )
    return store, request


def test_observed_association_is_calculated_and_persisted_without_causal_promotion(settings):
    store, request = saved_result(settings)
    result = DiagnosticsService(store).diagnose(request)
    content = result["content"]
    assert content["qualityGate"]["status"] == "passed"
    assert content["evidenceLevel"] == "association"
    assert content["association"]["riskDifference"] == pytest.approx(0.5)
    assert content["association"]["relativeRisk"] == pytest.approx(4)
    assert content["association"]["riskDifference95Interval"] is not None
    assert content["causalReadiness"]["engineeringConfirmed"] is False
    assert content["causalReadiness"]["causalEstimatorExecuted"] is False
    assert content["causalReadiness"]["status"] == "not_identified"
    assert content["methodExecution"]["automaticImputation"] is False
    persisted = store.get(request.run_id, 1, result["artifactId"])
    assert persisted["parentIds"] == [request.result_artifact_id]
    assert (
        persisted["content"]["provenance"]["resultSha256"]
        == store.get(request.run_id, 1, request.result_artifact_id)["sha256"]
    )


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"truncated": True}, "incomplete_result"),
        ({"truncated": None}, "incomplete_result"),
    ],
)
def test_truncated_or_unknown_population_blocks_risk_denominators(settings, overrides, code):
    store, request = saved_result(settings, **overrides)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["qualityGate"]["status"] == "blocked"
    assert content["association"] is None
    assert any(issue["code"] == code for issue in content["qualityGate"]["issues"])


@pytest.mark.parametrize(
    "change,code",
    [
        ({"failed": None}, "invalid_outcome"),
        ({"failed": 2}, "invalid_outcome"),
        ({"exposed": "yes"}, "invalid_exposure"),
        ({"device": None}, "null_keys"),
    ],
)
def test_null_and_undefined_event_values_are_not_imputed(settings, change, code):
    rows = binary_rows()
    rows[0].update(change)
    store, request = saved_result(settings, rows)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["association"] is None
    assert any(issue["code"] == code for issue in content["qualityGate"]["issues"])


def test_duplicate_join_rows_block_diagnostic_denominators(settings):
    rows = binary_rows()
    rows.append(rows[0].copy())
    store, request = saved_result(settings, rows)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["qualityGate"]["duplicateKeys"] == 1
    assert content["association"] is None


def test_repeated_device_observations_do_not_get_independent_row_uncertainty(settings):
    rows = binary_rows()
    rows[1]["device"] = rows[0]["device"]
    store, request = saved_result(settings, rows)
    request.key_fields = []
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["association"]["riskDifference95Interval"] is None
    assert content["evidenceLevel"] == "association"


def test_zero_comparison_denominator_and_zero_control_risk(settings):
    rows = binary_rows()
    for row in rows:
        row["exposed"] = 1
    store, request = saved_result(settings, rows)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["association"] is None
    assert any(issue["code"] == "empty_comparison_group" for issue in content["qualityGate"]["issues"])
    rows = binary_rows()
    for row in rows[6:]:
        row["failed"] = 0
    store, request = saved_result(settings, rows)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["association"]["relativeRisk"] is None
    assert content["association"]["riskDifference"] == pytest.approx(4 / 6)


def test_missing_explicit_fields_returns_actionable_quality_gate(settings):
    store, request = saved_result(settings)
    request.exposure_field = None
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["qualityGate"]["status"] == "blocked"
    assert content["evidenceLevel"] == "observed"
    assert any("exposure" in text for text in content["missingInputs"])


def test_direction_requires_event_times_and_cannot_promote_causal_claim(settings):
    store, request = saved_result(settings)
    request.observed_at_field = "observed"
    request.exposure_at_field = "exposure_time"
    request.outcome_at_field = "outcome_time"
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["causalReadiness"]["timingChecked"] is True
    assert content["evidenceLevel"] == "association"
    assert content["causalReadiness"]["status"] == "not_identified"
    rows = binary_rows()
    rows[0]["exposure_time"] = "2026-10-11T09:00:00+08:00"
    store, request = saved_result(settings, rows)
    request.exposure_at_field = "exposure_time"
    request.outcome_at_field = "outcome_time"
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["causalReadiness"]["timingChecked"] is False
    assert content["causalReadiness"]["directionViolations"] == 1


def test_window_mismatch_is_not_silently_filtered(settings):
    store, request = saved_result(settings)
    request.observed_at_field = "observed"
    request.window_start = datetime(2026, 10, 11, tzinfo=UTC)
    request.window_end = datetime(2026, 10, 12, tzinfo=UTC)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["association"] is None
    assert any(issue["code"] == "outside_window" for issue in content["qualityGate"]["issues"])


def test_unknown_timezone_prevents_validation_of_requested_window(settings):
    rows = binary_rows()
    rows[0]["observed"] = "2026-10-10T10:00:00"
    store, request = saved_result(settings, rows)
    request.observed_at_field = "observed"
    request.window_start = datetime(2026, 10, 9, tzinfo=UTC)
    request.window_end = datetime(2026, 10, 12, tzinfo=UTC)
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["association"] is None
    assert content["qualityGate"]["status"] == "blocked"


def test_successful_bi_query_node_is_accepted_with_owned_sql_provenance(settings):
    store, request = saved_result(settings)
    result = store.get(request.run_id, 1, request.result_artifact_id)
    different = store.add(
        request.run_id,
        1,
        "query_result",
        {**result["content"], "source": {"dataset_id": "industrial_pdm"}},
        result["parentIds"],
    )
    store.step(
        request.run_id,
        1,
        "BI_EXECUTE_NODE",
        status="succeeded",
        duration_ms=1,
        inputs=result["parentIds"],
        outputs=[different["artifactId"]],
    )
    request.result_artifact_id = different["artifactId"]
    content = DiagnosticsService(store).diagnose(request)["content"]
    assert content["evidenceLevel"] == "association"
    assert content["provenance"]["executionNodes"] == ["BI_EXECUTE_NODE"]


def test_anonymous_scania_has_no_physical_root_cause_mapping(settings):
    store, request = saved_result(settings, source={"dataset_id": "scania_component_x"})
    request.physical_root_cause = True
    with pytest.raises(DataAgentError, match="物理语义"):
        DiagnosticsService(store).diagnose(request)


def test_unexecuted_or_failed_or_wrong_kind_evidence_cannot_be_diagnosed(settings):
    store = ArtifactStore(settings)
    run_id = store.create(1, "Unverified")["runId"]
    artifact = store.add(run_id, 1, "query_result", {"column": ["x"], "data": []})
    request = DiagnosisRequest(agent_id=1, run_id=run_id, result_artifact_id=artifact["artifactId"])
    with pytest.raises(DataAgentError, match="successful execution"):
        DiagnosticsService(store).diagnose(request)
    store.step(
        run_id,
        1,
        "execute_query",
        status="failed",
        duration_ms=1,
        inputs=[],
        outputs=[artifact["artifactId"]],
    )
    with pytest.raises(DataAgentError, match="successful execution"):
        DiagnosticsService(store).diagnose(request)
    wrong = store.add(run_id, 1, "model_call", {"result": "claim"})
    request.result_artifact_id = wrong["artifactId"]
    with pytest.raises(DataAgentError, match="query_result"):
        DiagnosticsService(store).diagnose(request)


def test_row_count_tampering_and_cross_agent_access_are_rejected(settings):
    store, request = saved_result(settings, rowCount=99)
    with pytest.raises(DataAgentError, match="rowCount"):
        DiagnosticsService(store).diagnose(request)
    request.agent_id = 2
    with pytest.raises(NotFoundError):
        DiagnosticsService(store).diagnose(request)


def test_same_outcome_exposure_and_ambiguous_window_are_rejected():
    common = {"agent_id": 1, "run_id": "run", "result_artifact_id": "a" * 32}
    with pytest.raises(ValidationError):
        DiagnosisRequest(**common, outcome_field="x", exposure_field="x")
    with pytest.raises(ValidationError):
        DiagnosisRequest(**common, window_start="2026-10-10T00:00:00")


async def test_diagnostic_api_checks_api_key_and_agent_ownership(app, client, settings):
    if not any(getattr(route, "path", None) == "/api/diagnostics" for route in app.routes):
        app.include_router(router)
    async with app.state.database.session_factory() as session:
        session.add_all(
            [
                Agent(id=1, name="Protected", api_key_enabled=1, api_key=hash_api_key("test-key")),
                Agent(id=2, name="Other"),
            ]
        )
        await session.commit()
    _, request = saved_result(settings)
    payload = request.model_dump(mode="json")
    assert (await client.post("/api/diagnostics", json=payload)).status_code == 401
    good = await client.post("/api/diagnostics", json=payload, headers={"X-API-Key": "test-key"})
    assert good.status_code == 200
    assert good.json()["content"]["evidenceLevel"] == "association"
    payload["agent_id"] = 2
    assert (await client.post("/api/diagnostics", json=payload)).status_code == 404
