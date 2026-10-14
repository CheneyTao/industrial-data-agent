"""Read-only, explicitly mapped comparison against the independently verified demo baseline.

The mapping and baseline are NEVER included in model prompts. This is acceptance
code for one scenario, not application query routing or report generation.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    args = parser.parse_args()
    proof = json.loads(args.evidence.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    index = {item["artifactId"]: item for item in proof["artifacts"]}
    quarters = {item["model"]: item for item in baseline["quarterComparison"]}
    months = {(item["model"], item["month"]): item for item in baseline["monthlyOee"]}
    machines = {item["machineID"]: item for item in baseline["top5DowntimeQ4"]}
    aliases = {f"{letter} 型机组": f"model{i}" for i, letter in enumerate("ABCD", 1)}
    checks, covered = [], set()

    def check(name, actual, expected, tolerance="0"):
        if isinstance(actual, (str, int, float, Decimal)) and isinstance(
            expected, (str, int, float, Decimal)
        ):
            try:
                passed = abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal(tolerance)
            except Exception:
                passed = actual == expected
        else:
            passed = actual == expected
        checks.append({"check": name, "passed": passed, "actual": str(actual), "expected": str(expected)})

    def rows(spec):
        artifact = index[spec["resultId"]]
        assert artifact["kind"] == "query_result"
        check("not-truncated:" + spec["resultId"], artifact["content"]["truncated"], False)
        return artifact["content"]["data"]

    for spec in mapping["quarters"]:
        data = rows(spec)
        check("quarter-groups:" + spec["resultId"], len(data), 8)
        seen = set()
        for row in data:
            model = aliases.get(row[spec["modelField"]], row[spec["modelField"]])
            quarter = row[spec["quarterField"]]
            seen.add((model, quarter))
            expected = quarters[model][quarter]
            for field, definition in spec["metrics"].items():
                check(
                    f"quarter:{model}:{quarter}:{field}",
                    row[field],
                    expected[definition["baselineKey"]],
                    definition.get("tolerance", "0"),
                )
                covered.add(definition["role"])
        check("quarter-coverage:" + spec["resultId"], len(seen), 8)
    check("required-quarter-metrics", covered, {"oee", "downtime", "failures", "cost"})

    spec = mapping["top5"]
    data = rows(spec)
    check("top5-order", [row[spec["machineField"]] for row in data], list(machines))
    for row in data:
        machine = row[spec["machineField"]]
        check(
            f"top5-downtime:{machine}",
            row[spec["downtimeField"]],
            machines[machine]["downtime_hours"],
            "0.000001",
        )

    spec = mapping["monthly"]
    data = rows(spec)
    seen = set()
    for row in data:
        key = (aliases.get(row[spec["modelField"]], row[spec["modelField"]]), row[spec["monthField"]])
        seen.add(key)
        check(
            f"monthly-oee:{key}",
            row[spec["valueField"]],
            months[key]["avg_oee_pct"],
            spec.get("tolerance", "0.000501"),
        )
    check("monthly-groups", len(data), 24)
    check("monthly-coverage", len(seen), 24)

    machine_coverage = set()
    for spec in mapping.get("machineAggregates", []):
        data = rows(spec)
        check("machine-groups:" + spec["resultId"], len(data), 5)
        for row in data:
            machine = row[spec["machineField"]]
            for field, expected_field in spec["metrics"].items():
                check(f"machine:{machine}:{field}", row[field], machines[machine][expected_field], "0.000001")
                machine_coverage.add(expected_field)
    for spec in mapping.get("machineDetails", []):
        data = rows(spec)
        for machine, expected in machines.items():
            records = [row for row in data if row[spec["machineField"]] == machine]
            if spec.get("faultIdField"):
                faults = {
                    row[spec["faultIdField"]] for row in records if row.get(spec["faultIdField"]) is not None
                }
                check(f"machine-faults:{machine}", len(faults), int(expected["failure_count"]))
                machine_coverage.add("failure_count")
            if spec.get("workOrderIdField"):
                orders = [row for row in records if row.get(spec["workOrderIdField"]) is not None]
                check(f"machine-orders:{machine}", len(orders), int(expected["work_order_count"]))
                total = sum(Decimal(str(row[spec["costField"]])) for row in orders)
                check(f"machine-cost:{machine}", total, expected["total_cost"], "0.000001")
                machine_coverage.update({"work_order_count", "total_cost"})
    check(
        "required-machine-cross-checks",
        {"failure_count", "work_order_count", "total_cost"} <= machine_coverage,
        True,
    )
    result = {
        "runId": proof["manifest"]["runId"],
        "baselineRunId": baseline["baselineRunId"],
        "passed": sum(item["passed"] for item in checks),
        "total": len(checks),
        "failures": [item for item in checks if not item["passed"]],
        "checks": checks,
        "boundary": "Independent SQL-result checks, not verification of every narrative claim.",
    }
    (args.evidence.parent / "baseline_comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in result.items() if key != "checks"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
