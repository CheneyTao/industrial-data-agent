"""Independent report acceptance: exact structured facts, not keyword grading.

Contract mode exercises the renderer/calculator, NOT model intelligence. Live
mode sends only public questions and rows to the configured model. Its oracle
never enters the prompt. Every trial retains its full artifact trace.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path
from typing import Any

from dataagent.artifacts import ArtifactStore
from dataagent.calculations import AggregateSpec, CompareSpec, Metric, calculate_result
from dataagent.config import Settings
from dataagent.database import Database
from dataagent.llm import LLMClient
from dataagent.report_claims import ExtremeClaim, ReportClaimsPlan, RowClaim, compile_report
from dataagent.reports import generate_grounded_report
from dataagent.version import build_identity


async def evaluate(output: Path, trials: int = 5, live: bool = False) -> dict[str, Any]:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    # Live configuration is read normally; no credentials are written into logs.
    config = Settings() if live else Settings(_env_file=None, model_mode="mock")
    config.vector_file_path = output / "vectors.json"
    store = ArtifactStore(config)
    db = Database(config) if live else None
    llm = LLMClient(config)
    checks = []
    try:
        for trial in range(trials):
            rows = [
                {"device": "A", "failures": 7},
                {"device": "B", "failures": 7},
                {"device": "C", "failures": None},
                {"device": "D", "failures": 0},
            ]
            random.Random(trial).shuffle(rows)
            run = store.create(1, "列出本次返回行故障次数最大的所有并列设备，保留空值语义。")["runId"]
            sql = store.add(
                run,
                1,
                "sql",
                {
                    "sql": "SELECT device, failures FROM acceptance_fixture",
                    "sourceId": None,
                    "sourceName": "synthetic acceptance only",
                    "tables": ["acceptance_fixture"],
                },
            )
            query = store.add(
                run,
                1,
                "query_result",
                {"column": ["device", "failures"], "data": rows, "rowCount": 4, "truncated": bool(trial % 2)},
                [sql["artifactId"]],
            )
            store.step(
                run,
                1,
                "execute_query",
                status="succeeded",
                duration_ms=0,
                inputs=[sql["artifactId"]],
                outputs=[query["artifactId"]],
            )
            if live:
                async with db.session_factory() as session:  # type: ignore[union-attr]
                    await generate_grounded_report(
                        store,
                        run,
                        1,
                        session,
                        llm,
                        requirements=store.load(run, 1)["question"],
                        results=None,
                        summary="仅使用实际查询证据",
                    )
                record = next(
                    a for a in reversed(store.load(run, 1)["artifacts"]) if a["kind"] == "report_claims"
                )
                compiled = store.get(run, 1, record["artifactId"])["content"]
            else:
                _, compiled = compile_report(
                    store,
                    run,
                    1,
                    ReportClaimsPlan(
                        claims=[
                            ExtremeClaim(
                                type="extremum",
                                source_id=query["artifactId"],
                                measure="failures",
                                labels=["device"],
                                operation="max",
                            )
                        ]
                    ),
                    [],
                )
                store.add(run, 1, "report_claims", compiled, [query["artifactId"]])
            ranking = [
                claim
                for claim in compiled["claims"]
                if claim["declaration"].get("type") == "extremum"
                and claim["declaration"].get("measure") == "failures"
                and claim["declaration"].get("operation") == "max"
            ]
            labels = {b["value"] for c in ranking for b in c["bindings"] if b["field"] == "device"}
            values = {b["value"] for c in ranking for b in c["bindings"] if b["field"] == "failures"}
            # These are independent hidden answers, never supplied to the model.
            checks.append(
                {
                    "case": "all_tied_maxima",
                    "trial": trial,
                    "runId": run,
                    "passed": labels == {"A", "B"} and values == {7},
                    "expectedLabels": ["A", "B"],
                    "actualLabels": sorted(labels),
                }
            )
            checks.append(
                {
                    "case": "bounded_scope",
                    "trial": trial,
                    "runId": run,
                    "passed": bool(ranking)
                    and all(("截断" in c["scope"]) == bool(trial % 2) for c in ranking),
                }
            )
            count = calculate_result(
                store,
                run,
                1,
                AggregateSpec(
                    type="aggregate",
                    result_artifact_id=query["artifactId"],
                    metrics=[
                        Metric(field="failures", operation="count", name="non_null"),
                        Metric(field="failures", operation="count_distinct", name="distinct"),
                    ],
                ),
            )
            checks.append(
                {
                    "case": "count_not_distinct",
                    "trial": trial,
                    "runId": run,
                    "passed": count["content"]["data"] == [{"non_null": 3, "distinct": 2}],
                }
            )
            periods = store.add(
                run,
                1,
                "query_result",
                {
                    "column": ["period", "events"],
                    "data": [{"period": "before", "events": 0}, {"period": "after", "events": 3}],
                    "rowCount": 2,
                    "truncated": False,
                },
                [sql["artifactId"]],
            )
            store.step(
                run,
                1,
                "execute_query",
                status="succeeded",
                duration_ms=0,
                inputs=[sql["artifactId"]],
                outputs=[periods["artifactId"]],
            )
            comparison = calculate_result(
                store,
                run,
                1,
                CompareSpec(
                    type="compare",
                    result_artifact_id=periods["artifactId"],
                    period_field="period",
                    baseline="before",
                    current="after",
                    measures=["events"],
                ),
            )
            _, zero_claims = compile_report(
                store,
                run,
                1,
                ReportClaimsPlan(claims=[RowClaim(type="row", source_id=comparison["artifactId"], row=0)]),
                [comparison["artifactId"]],
            )
            bound = {b["field"]: b["value"] for b in zero_claims["claims"][0]["bindings"]}
            checks.append(
                {
                    "case": "zero_baseline_is_unavailable",
                    "trial": trial,
                    "runId": run,
                    "passed": bound["difference"] == "3"
                    and bound["change_pct"] is None
                    and bound["status"] == "zero_baseline",
                }
            )
            store.finish(run, 1, "succeeded")
    finally:
        if db:
            await db.close()
        if llm.telemetry:
            await llm.telemetry.close()
    report = {
        "build": build_identity(),
        "mode": "live-model" if live else "deterministic-contract",
        "notice": (
            "Only all_tied_maxima/bounded_scope test model selection in live mode. "
            "Other cases test controlled arithmetic."
        ),
        "trials": trials,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "checks": checks,
    }
    (output / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5, choices=range(1, 11))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.output, args.trials, args.live))
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] == result["total"] else 1)


if __name__ == "__main__":
    main()
