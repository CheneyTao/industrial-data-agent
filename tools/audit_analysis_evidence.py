"""Independent checks of saved hashes, lineage, plotted points and full report inputs.

This intentionally does not call the production chart renderer or profile builder.
Business SQL/report semantics still require a separate, scenario-specific baseline audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def audit(path: Path):
    evidence = json.loads((path / "evidence.json").read_text(encoding="utf-8"))
    manifest, artifacts = evidence["manifest"], evidence["artifacts"]
    index = {item["artifactId"]: item for item in artifacts}
    descriptors = {item["artifactId"]: item for item in manifest["artifacts"]}
    checks, charts = [], []

    def check(name, passed):
        checks.append({"check": name, "passed": bool(passed)})

    for item in artifacts:
        aid, content, kind = item["artifactId"], item["content"], item["kind"]
        serialized = json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        check(
            f"hash:{aid}",
            hashlib.sha256(serialized.encode()).hexdigest() == item["sha256"] == descriptors[aid]["sha256"],
        )
        check(f"parents:{aid}", all(parent in index for parent in item["parentIds"]))
        if kind == "query_result":
            check(f"sql-source:{aid}", index[item["parentIds"][0]]["kind"] == "sql")
            check(f"row-count:{aid}", content["rowCount"] == len(content["data"]))
        elif kind == "chart":
            query = index[content["resultArtifactId"]]
            check(
                f"chart-source:{aid}",
                query["kind"] == "query_result" and query["artifactId"] in item["parentIds"],
            )
            rows, spec, option = query["content"]["data"], content["spec"], content["option"]
            dimension = spec.get("dimension", spec.get("x"))
            measures = spec.get("measures", spec.get("y"))
            group = spec.get("group_by", spec.get("category"))
            typ = spec["type"]
            labels = ["(空值)" if row[dimension] is None else str(row[dimension]) for row in rows]
            if isinstance(option.get("xAxis"), list):
                check(
                    f"separate-measure-axes:{aid}",
                    len(option["xAxis"]) == len(measures) == len(option["yAxis"]),
                )
                for panel, measure in enumerate(measures):
                    series_list = [
                        s
                        for s in option["series"]
                        if s.get("xAxisIndex") == panel and s.get("yAxisIndex") == panel
                    ]
                    if typ == "scatter":
                        expected = [
                            [number(row[dimension]), number(row[measure])]
                            for row in rows
                            if number(row[measure]) is not None
                        ]
                        check(
                            f"panel-points:{aid}:{measure}",
                            len(series_list) == 1 and series_list[0]["data"] == expected,
                        )
                    else:
                        categories = list(dict.fromkeys(labels))
                        axis = option["yAxis" if typ == "bar" else "xAxis"][panel]
                        check(f"panel-labels:{aid}:{measure}", axis["data"] == categories)
                        groups = list(dict.fromkeys(str(row[group]) for row in rows)) if group else [None]
                        check(f"panel-series-count:{aid}:{measure}", len(series_list) == len(groups))
                        for group_value, series in zip(groups, series_list, strict=True):
                            source = {
                                label: number(row[measure])
                                for label, row in zip(labels, rows, strict=True)
                                if not group or str(row[group]) == group_value
                            }
                            check(
                                f"panel-values:{aid}:{measure}:{group_value}",
                                series["data"] == [source.get(label) for label in categories],
                            )
            elif typ == "heatmap":
                expected = [
                    (label, str(row[group]), number(row[measures[0]]))
                    for label, row in zip(labels, rows, strict=True)
                    if row[measures[0]] is not None
                ]
                actual = [
                    (option["xAxis"]["data"][x], option["yAxis"]["data"][y], value)
                    for x, y, value in option["series"][0]["data"]
                ]
                check(f"plotted-values:{aid}", expected == actual)
            elif typ == "pie":
                expected = [
                    {"name": label, "value": number(row[measures[0]])}
                    for label, row in zip(labels, rows, strict=True)
                ]
                check(f"plotted-values:{aid}", option["series"][0]["data"] == expected)
            elif typ == "scatter":
                for measure, series in zip(measures, option["series"], strict=True):
                    expected = [
                        [number(row[dimension]), number(row[measure])]
                        for row in rows
                        if number(row[measure]) is not None
                    ]
                    check(f"plotted-values:{aid}:{measure}", expected == series["data"])
            elif group:
                categories = list(dict.fromkeys(labels))
                check(f"labels:{aid}", option["yAxis" if typ == "bar" else "xAxis"]["data"] == categories)
                groups = list(dict.fromkeys(str(row[group]) for row in rows))
                series_index = 0
                for value in groups:
                    for measure in measures:
                        source = {
                            label: number(row[measure])
                            for label, row in zip(labels, rows, strict=True)
                            if str(row[group]) == value
                        }
                        series = option["series"][series_index]
                        check(
                            f"series-values:{aid}:{value}:{measure}",
                            series["data"] == [source.get(label) for label in categories],
                        )
                        series_index += 1
                check(f"series-count:{aid}", series_index == len(option["series"]))
            else:
                check(f"labels:{aid}", option["yAxis" if typ == "bar" else "xAxis"]["data"] == labels)
                for measure, series in zip(measures, option["series"], strict=True):
                    check(
                        f"plotted-values:{aid}:{measure}",
                        series["data"] == [number(row[measure]) for row in rows],
                    )
            charts.append(
                {
                    "artifactId": aid,
                    "type": typ,
                    "dimension": dimension,
                    "measures": measures,
                    "group_by": group,
                }
            )
        elif kind == "report_input":
            for query in content["results"]:
                original = index[query["artifactId"]]["content"]
                restored = [dict(zip(query["columns"], row, strict=True)) for row in query["rows"]]
                check(
                    f"full-report-rows:{query['artifactId']}",
                    restored == original["data"] and query["allReturnedRowsIncluded"],
                )
                check(f"truncation-truth:{query['artifactId']}", query["truncated"] == original["truncated"])
            for derived in content.get("calculations", []):
                original = index[derived["artifactId"]]["content"]
                restored = [dict(zip(derived["columns"], row, strict=True)) for row in derived["rows"]]
                check(f"full-calculation-input:{derived['artifactId']}", restored == original["data"])
        elif kind == "result_calculation":
            spec = content["spec"]
            source = index[spec["result_artifact_id"]]
            rows = source["content"]["data"]
            check(
                f"calculation-source:{aid}",
                source["kind"] == "query_result" and source["artifactId"] in item["parentIds"],
            )
            check(
                f"calculation-scope:{aid}", content["truncated"] == bool(source["content"].get("truncated"))
            )
            fields = spec["group_by"]
            if spec["type"] == "aggregate":
                for condition in spec["filters"]:
                    rows = [
                        row
                        for row in rows
                        if (
                            row[condition["field"]] is not None
                            if condition["operation"] == "not_null"
                            else row[condition["field"]] == condition["value"]
                        )
                    ]
            else:
                rows = [
                    row for row in rows if row[spec["period_field"]] in (spec["baseline"], spec["current"])
                ]
            check(f"calculation-row-count:{aid}", content["matchedRows"] == len(rows))
            groups = {tuple(row[field] for field in fields) for row in rows} or (
                {()} if not fields else set()
            )
            check(
                f"calculation-groups:{aid}",
                groups == {tuple(row[field] for field in fields) for row in content["data"]},
            )
            for i, output in enumerate(content["data"]):
                members = [row for row in rows if all(row[field] == output[field] for field in fields)]
                expected = {}
                if spec["type"] == "aggregate":
                    for metric in spec["metrics"]:
                        values = [row[metric["field"]] for row in members if row[metric["field"]] is not None]
                        op = metric["operation"]
                        if op == "count":
                            value = len(values)
                        elif op == "count_distinct":
                            value = len(set(values))
                        elif not values:
                            value = None
                        else:
                            nums = [number(v) for v in values]
                            value = {
                                "sum": sum(nums),
                                "mean": sum(nums) / len(nums),
                                "min": min(nums),
                                "max": max(nums),
                            }[op]
                        expected[metric["name"]] = value
                else:
                    before = [row for row in members if row[spec["period_field"]] == spec["baseline"]]
                    after = [row for row in members if row[spec["period_field"]] == spec["current"]]
                    check(f"comparison-unique:{aid}:{i}", len(before) <= 1 and len(after) <= 1)
                    a = number(before[0][output["measure"]]) if before else None
                    b = number(after[0][output["measure"]]) if after else None
                    delta = b - a if a is not None and b is not None else None
                    expected = {
                        "baseline": a,
                        "current": b,
                        "difference": delta,
                        "change_pct": delta / a * 100 if a and delta is not None else None,
                    }
                for field, value in expected.items():
                    actual = number(output[field])
                    check(
                        f"calculation-value:{aid}:{i}:{field}",
                        actual is None
                        if value is None
                        else actual is not None and math.isclose(actual, value, rel_tol=1e-10, abs_tol=1e-8),
                    )
        elif kind == "analysis":
            result_id, chart_id = content["evidenceRefs"]
            check(f"analysis-source:{aid}", index[chart_id]["content"]["resultArtifactId"] == result_id)
            rows = index[result_id]["content"]["data"]
            for field, profile in content["profile"]["fields"].items():
                check(
                    f"nulls:{aid}:{field}",
                    sum(row.get(field) is None for row in rows) == profile["nullCount"],
                )
                if "mean" in profile:
                    values = [number(row[field]) for row in rows if row.get(field) is not None]
                    check(
                        f"stats:{aid}:{field}",
                        math.isclose(sum(values) / len(values), profile["mean"], rel_tol=1e-12)
                        and min(values) == profile["min"]
                        and max(values) == profile["max"],
                    )
        elif kind == "report":
            reviews = [
                index[parent] for parent in item["parentIds"] if index[parent]["kind"] == "report_review"
            ]
            # Historical reports predate the publication gate; explicitly do not call them verified.
            check(f"report-review-gate:{aid}", bool(reviews) and reviews[-1]["content"]["passed"])
        elif kind == "acceptance_review":
            check(f"independent-report-acceptance:{aid}", content["passed"])
    result = {
        "runId": manifest["runId"],
        "status": manifest["status"],
        "artifacts": len(artifacts),
        "passed": sum(item["passed"] for item in checks),
        "total": len(checks),
        "failures": [item for item in checks if not item["passed"]],
        "charts": charts,
        "checks": checks,
    }
    (path / "evidence_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in result.items() if key != "checks"}, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    audit(parser.parse_args().directory)
