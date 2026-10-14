"""Append a reproducible, externally audited count counterexample; never edit the original report."""

from __future__ import annotations

import argparse
import base64
import json
from time import perf_counter

from dataagent.artifacts import ArtifactStore
from dataagent.calculations import AggregateSpec, calculate_result
from dataagent.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("finding_base64")
    finding = json.loads(base64.b64decode(parser.parse_args().finding_base64))
    store = ArtifactStore(get_settings())
    run, agent = finding["runId"], finding["agentId"]
    report = store.get(run, agent, finding["reportId"])
    if report["kind"] != "report" or report["sha256"] != finding["reportSha256"]:
        raise ValueError("Report version differs; cannot attach this counterexample")
    if report["content"].count(finding["quote"]) != 1:
        raise ValueError("Quoted claim must match one original report fragment")
    started = perf_counter()
    spec = AggregateSpec.model_validate(finding["calculation"])
    if (
        spec.group_by
        or len(spec.metrics) != 1
        or spec.metrics[0].operation not in {"count", "count_distinct"}
    ):
        raise ValueError("A count counterexample requires one ungrouped count metric")
    result = calculate_result(store, run, agent, spec)
    actual = result["content"]["data"][0][spec.metrics[0].name]
    if actual == finding["claimedCount"]:
        raise ValueError("The calculation does not contradict the claimed count")
    review = store.add(
        run,
        agent,
        "acceptance_review",
        {
            "passed": False,
            "reviewer": "Independent acceptance using explicit source-row predicates",
            "reportId": report["artifactId"],
            "quote": finding["quote"],
            "claimedCount": finding["claimedCount"],
            "actualCount": actual,
            "scope": "One reproducible counterexample, not a complete narrative verifier.",
            "explanation": finding["explanation"],
        },
        [report["artifactId"], spec.result_artifact_id, result["artifactId"]],
    )
    store.step(
        run,
        agent,
        "independent_report_acceptance",
        status="failed",
        duration_ms=(perf_counter() - started) * 1000,
        inputs=[report["artifactId"], result["artifactId"]],
        outputs=[review["artifactId"]],
    )
    store.finish(run, agent, "partial")
    print(
        json.dumps(
            {
                "runId": run,
                "status": "partial",
                "actualCount": actual,
                "acceptanceArtifactId": review["artifactId"],
                "businessRowsChanged": False,
            }
        )
    )


if __name__ == "__main__":
    main()
