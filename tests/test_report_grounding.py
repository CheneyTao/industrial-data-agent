from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from dataagent.artifacts import ArtifactStore
from dataagent.calculations import CalculationPlan
from dataagent.errors import ModelResponseError
from dataagent.llm import LLMClient
from dataagent.report_claims import CellClaim, ReportClaimsPlan, RowClaim
from dataagent.reports import (
    ReportEdit,
    ReportReview,
    apply_report_edits,
    evidence_appendix,
    generate_grounded_report,
    report_evidence,
)


def evidence(settings, source_id=1):
    store = ArtifactStore(settings)
    run_id = store.create(1, "Compare all rows")["runId"]
    sql = store.add(
        run_id,
        1,
        "sql",
        {
            "sourceId": source_id,
            "sourceName": "demo",
            "sql": "SELECT * FROM readings",
            "tables": ["readings"],
        },
    )
    rows = [{"id": i, "value": i if i % 2 else None} for i in range(60)]
    query = store.add(
        run_id,
        1,
        "query_result",
        {
            "column": ["id", "value"],
            "data": rows,
            "rowCount": len(rows),
            "truncated": False,
        },
        [sql["artifactId"]],
    )
    store.step(
        run_id,
        1,
        "execute_query",
        status="succeeded",
        duration_ms=1,
        inputs=[sql["artifactId"]],
        outputs=[query["artifactId"]],
    )
    return store, run_id, query, rows


def test_report_input_is_lossless_not_a_repeated_snapshot(settings):
    store, run_id, query, rows = evidence(settings)
    pack = report_evidence(store, run_id, 1)
    result = pack["results"][0]
    assert result["artifactId"] == query["artifactId"]
    assert result["allReturnedRowsIncluded"] and not result["truncated"]
    assert [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]] == rows


def test_python_report_implementation_is_bound_to_each_attempt(settings):
    store, run_id, query, _ = evidence(settings)
    first = store.add(run_id, 1, "python_code", "import pandas as pd\nraise ValueError('bad')")
    failed = store.add(
        run_id, 1, "python_result", '{"success":false}', [query["artifactId"], first["artifactId"]]
    )
    second = store.add(
        run_id, 1, "python_code", 'import json\n# import pandas\nlabel = "import numpy"\nprint("{}")'
    )
    succeeded = store.add(
        run_id,
        1,
        "python_result",
        '{"value":10}',
        [query["artifactId"], first["artifactId"], failed["artifactId"], second["artifactId"]],
    )
    rows = report_evidence(store, run_id, 1)["otherEvidence"]
    profiles = {row["artifactId"]: row["implementation"] for row in rows}
    assert profiles[failed["artifactId"]]["codeArtifactId"] == first["artifactId"]
    assert profiles[failed["artifactId"]]["declaredImportRoots"] == ["pandas"]
    assert profiles[succeeded["artifactId"]]["codeArtifactId"] == second["artifactId"]
    assert profiles[succeeded["artifactId"]]["declaredImportRoots"] == ["json"]
    assert profiles[succeeded["artifactId"]]["codeSha256"] == second["sha256"]


@pytest.mark.parametrize(
    ("code", "parsed", "imports"),
    [
        ("from pandas import DataFrame\nimport numpy.linalg as la", True, ["numpy", "pandas"]),
        ("# import pandas\nprint('from numpy import array')", True, []),
        ("import invalid syntax !", False, []),
        ({"not": "source code"}, False, []),
    ],
)
def test_python_code_profile_is_static_and_tolerates_historical_failures(settings, code, parsed, imports):
    store, run_id, _, _ = evidence(settings)
    source = store.add(run_id, 1, "python_code", code)
    store.add(run_id, 1, "python_result", "{}", [source["artifactId"]])
    profile = report_evidence(store, run_id, 1)["otherEvidence"][0]["implementation"]
    assert profile["available"]
    assert profile["sourceParsed"] is parsed
    assert profile["declaredImportRoots"] == imports
    assert "not proof" in profile["scope"]


def test_report_missing_code_stays_unknown_and_chart_does_not_claim_row_filter(settings):
    store, run_id, query, _ = evidence(settings)
    store.add(run_id, 1, "python_result", '{"count":60}', [query["artifactId"]])
    store.add(
        run_id,
        1,
        "chart",
        {"spec": {"type": "column"}, "resultArtifactId": query["artifactId"]},
        [query["artifactId"]],
    )
    pack = report_evidence(store, run_id, 1)
    assert pack["otherEvidence"][0]["implementation"] == {"available": False}
    assert pack["charts"][0]["businessRowFilterApplied"] is False
    assert pack["results"][0]["returnedRows"] == 60


@pytest.mark.parametrize("source_id", [1, None])
def test_evidence_appendix_labels_unregistered_sources_without_none(settings, source_id):
    store, run_id, _, _ = evidence(settings, source_id)
    appendix = evidence_appendix(store, run_id, 1)
    assert "None" not in appendix
    assert ("数据源编号 `1`" if source_id == 1 else "本轮私有分析来源") in appendix


def test_report_never_silently_compacts_evidence(settings):
    settings.chat_context_window_tokens = 1024
    llm = LLMClient(settings)
    with pytest.raises(ModelResponseError, match="not silently truncated"):
        llm._budgeted_messages(
            system="rules", user="data " * 2000, max_output_tokens=128, attempt=0, preserve_input=True
        )


async def test_report_revises_invalid_coordinates_and_keeps_rejected_declaration(settings):
    store, run_id, query, _ = evidence(settings)
    llm = SimpleNamespace(
        settings=settings,
        complete=AsyncMock(
            side_effect=[
                CalculationPlan(calculations=[]),
                ReportClaimsPlan(claims=[RowClaim(type="row", source_id=query["artifactId"], row=100)]),
                ReportClaimsPlan(
                    claims=[CellClaim(type="cell", source_id=query["artifactId"], row=1, field="value")]
                ),
            ]
        ),
    )
    report = await generate_grounded_report(
        store,
        run_id,
        1,
        None,
        llm,
        requirements="question",
        results={"notAuthoritative": True},
        summary="summary",
    )
    assert report.startswith("# 数据分析报告")
    assert "| value |\n| --- |\n| 1 |" in report
    assert "notAuthoritative" not in llm.complete.call_args_list[1].kwargs["user"]
    kinds = [item["kind"] for item in store.load(run_id, 1)["artifacts"]]
    assert kinds.count("report_claim_plan") == 2 and kinds.count("report_claims") == 1


async def test_invalid_claim_failure_does_not_publish_unverified_text(settings):
    store, run_id, query, _ = evidence(settings)
    llm = SimpleNamespace(
        settings=settings,
        complete=AsyncMock(
            side_effect=[
                CalculationPlan(calculations=[]),
                ReportClaimsPlan(
                    claims=[CellClaim(type="cell", source_id=query["artifactId"], row=0, field="invented")]
                ),
                ReportClaimsPlan(claims=[RowClaim(type="row", source_id=query["artifactId"], row=60)]),
            ]
        ),
    )
    with pytest.raises(ModelResponseError, match="unpublished"):
        await generate_grounded_report(store, run_id, 1, None, llm, requirements="q", results={}, summary="s")
    assert llm.complete.call_count == 3
    assert not any(item["kind"] == "report" for item in store.load(run_id, 1)["artifacts"])


async def test_report_identifiers_remain_data_not_evidence_references(settings):
    store, run_id, query, _ = evidence(settings)
    identifier = "e0cca9501076480f91fbfc2917828745"
    # Report no longer scans prose for apparent UUID fragments. Only typed IDs
    # are resolved, so identifiers cannot be confused with citations.
    llm = SimpleNamespace(
        settings=settings,
        complete=AsyncMock(
            side_effect=[
                CalculationPlan(calculations=[]),
                ReportClaimsPlan(claims=[RowClaim(type="row", source_id=query["artifactId"], row=0)]),
            ]
        ),
    )
    report = await generate_grounded_report(
        store, run_id, 1, None, llm, requirements="q", results={}, summary="s"
    )
    assert report.startswith("# 数据分析报告") and query["artifactId"] in report
    assert identifier not in report


async def test_report_rejects_unknown_typed_evidence_id(settings):
    store, run_id, _, _ = evidence(settings)
    settings.report_revision_retries = 0
    unknown_id = "f" * 32

    llm = SimpleNamespace(
        settings=settings,
        complete=AsyncMock(
            side_effect=[
                CalculationPlan(calculations=[]),
                ReportClaimsPlan(claims=[RowClaim(type="row", source_id=unknown_id, row=0)]),
            ]
        ),
    )
    with pytest.raises(ModelResponseError, match="unpublished"):
        await generate_grounded_report(store, run_id, 1, None, llm, requirements="q", results={}, summary="s")
    declaration = next(a for a in store.load(run_id, 1)["artifacts"] if a["kind"] == "report_claim_plan")
    content = store.get(run_id, 1, declaration["artifactId"])["content"]
    assert content["status"] == "rejected"
    assert content["claims"][0]["source_id"] == unknown_id


def test_review_verdict_cannot_hide_issues():
    with pytest.raises(ValidationError):
        ReportReview(passed=True, issues=["incorrect value"])
    with pytest.raises(ValidationError):
        ReportReview(passed=False, issues=[])
    with pytest.raises(ValidationError):
        ReportReview(passed=False, issues=["x" * 401])


def test_report_corrections_preserve_unrelated_text_and_validate_matches():
    assert apply_report_edits("good; bad; keep", [ReportEdit(old="bad", new="fixed")]) == "good; fixed; keep"
    for text, edits in [
        ("body", []),
        ("body", [ReportEdit(old="missing", new="x")]),
        ("bad bad", [ReportEdit(old="bad", new="x")]),
        ("bad claim", [ReportEdit(old="bad", new="x"), ReportEdit(old="bad claim", new="y")]),
    ]:
        with pytest.raises(ModelResponseError):
            apply_report_edits(text, edits)
