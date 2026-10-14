"""Live isolated acceptance: natural-language chart choice and injected failover.

Uses predefined test rows, not SQL results. Never changes the active model or
injects failures into the running web/MCP service. All requests use its existing
model endpoint. Evidence is stored under a separate explicitly labelled run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from time import perf_counter

import httpx

from dataagent.artifacts import ArtifactStore
from dataagent.config import Settings
from dataagent.database import Database
from dataagent.llm import LLMClient
from dataagent.model_calls import capture_model_calls
from dataagent.prompts import render_prompt
from dataagent.visualization import ChartSpec, chart_from_result, result_profile

CASES = [
    (
        "trend",
        "2015 年 7 至 12 月设备 OEE 的走势如何？",
        [{"month": f"2015-{m:02}", "oee_pct": 89 + m / 5} for m in range(7, 13)],
        {"line"},
    ),
    (
        "comparison",
        "比较 A、B、C、D 四类设备的平均 OEE。",
        [{"model": m, "oee_pct": v} for m, v in zip("ABCD", [88, 90, 91, 92], strict=True)],
        {"column", "bar"},
    ),
    (
        "ranking",
        "哪几台设备停机最严重？按停机小时数比较，设备全称要能看清。",
        [
            {"machine_name": "一号生产车间东侧冷却循环泵设备", "downtime_hours": 120},
            {"machine_name": "二号装配车间西侧精密输送设备", "downtime_hours": 100},
            {"machine_name": "三号加工车间南侧数控切削设备", "downtime_hours": 70},
        ],
        {"bar"},
    ),
    (
        "relationship",
        "运行温度和振动强度之间有怎样的关联？不要把相关当因果。",
        [{"temperature_c": t, "vibration_mm_s": v} for t, v in [(50, 1.1), (60, 1.5), (70, 2.4), (80, 3.6)]],
        {"scatter"},
    ),
    (
        "matrix",
        "不同型号在各个月份的 OEE 差异集中在哪里？同时比较型号和月份。",
        [
            {"month": f"2015-{m:02}", "model": k, "oee_pct": 85 + m / 2 + i}
            for m in range(7, 10)
            for i, k in enumerate("ABC")
        ],
        {"heatmap", "line"},
    ),
    (
        "matrix_color",
        "把每个月与各型号交叉组合的 OEE 用颜色深浅呈现，便于找出偏低的组合。",
        [
            {"month": f"2015-{m:02}", "model": k, "oee_pct": 85 + m / 2 + i}
            for m in range(7, 10)
            for i, k in enumerate("ABC")
        ],
        {"heatmap"},
    ),
    (
        "composition",
        "轴承、传感器、电源这三类互斥故障分别占全部故障的多少？",
        [{"component": k, "fault_count": v} for k, v in [("轴承", 50), ("传感器", 30), ("电源", 20)]],
        {"pie"},
    ),
    (
        "additive_components",
        "比较各车间总停机时间的构成：故障停机和保养停机互不重叠，单位都是小时。",
        [
            {"workshop": k, "failure_hours": v, "maintenance_hours": 5}
            for k, v in [("A", 20), ("B", 10), ("C", 30)]
        ],
        {"stacked_bar"},
    ),
    (
        "details",
        "列出这些工单的编号、备注和处理结果，便于逐条核对。",
        [
            {"work_order": "WO-01", "note": "轴承检查", "status": "已关闭"},
            {"work_order": "WO-02", "note": "等待备件", "status": "处理中"},
        ],
        {"table"},
    ),
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=[item[0] for item in CASES])
    parser.add_argument("--skip-failover", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    settings = Settings()
    # A small isolated diagnostic budget, not a production timeout change.
    settings.model_http_timeout_seconds = 25
    database = Database(settings)
    store = ArtifactStore(settings)
    run = store.create(10, "图表选型与备用切换隔离验收：预设测试数据，不是业务 SQL；故障为本测试进程注入。")
    run_id = run["runId"]
    print(json.dumps({"runId": run_id, "kind": "isolated_chart_and_failover_acceptance"}), flush=True)
    try:
        async with database.session_factory() as session:
            active = await LLMClient(settings).registry.active(session, "CHAT")

        async def get_active(_session, _model_type):
            return active

        template = render_prompt(
            "data-view-analyze", format=json.dumps(ChartSpec.model_json_schema(), ensure_ascii=False)
        )
        system, _, _ = template.partition("=== 用户输入 ===")
        semaphore = asyncio.Semaphore(2)

        async def case(name, question, rows, expected, blocked=()):
            async with semaphore:
                llm = LLMClient(settings)
                llm.registry.active = get_active
                if blocked:
                    original_client = llm._client

                    def injected_client(config):
                        client = original_client(config)
                        original_send = client.send

                        async def send(request, **kwargs):
                            model = json.loads(request.content).get("model")
                            if model in blocked:
                                raise httpx.ReadTimeout("Injected test timeout", request=request)
                            return await original_send(request, **kwargs)

                        client.send = send
                        return client

                    llm._client = injected_client

                source = store.add(
                    run_id,
                    10,
                    "test_input",
                    {
                        "case": name,
                        "question": question,
                        "rows": rows,
                        "expectedTypes": sorted(expected),
                        "injectedTimeoutModels": list(blocked),
                        "notice": "Predefined test data, not a model-generated SQL result.",
                    },
                )
                calls, outputs = [], []

                def record(call):
                    calls.append(call)
                    artifact = store.add(
                        run_id, 10, "model_call", {"case": name, **call}, [source["artifactId"]]
                    )
                    outputs.append(artifact["artifactId"])

                started = perf_counter()
                spec = None
                try:
                    with capture_model_calls(record):
                        async with asyncio.timeout(90):
                            spec = await llm.complete(
                                None,
                                system=system,
                                user=json.dumps(
                                    {
                                        "question": question,
                                        "columns": list(rows[0]),
                                        "profile": result_profile({"column": list(rows[0]), "data": rows}),
                                        "sampleRows": rows[:20],
                                    },
                                    ensure_ascii=False,
                                ),
                                purpose="data-view",
                                json_model=ChartSpec,
                            )
                    option = chart_from_result({"data": rows, "column": list(rows[0])}, spec)
                    result = {
                        "case": name,
                        "passed": spec.type in expected,
                        "chosenType": spec.type,
                        "spec": spec.model_dump(),
                        "option": option,
                        "modelsAttempted": [c["model"] for c in calls],
                        "injectedTimeoutModels": list(blocked),
                    }
                    if name.startswith("matrix_") and spec.type == "line":
                        result["passed"] = result["passed"] and spec.group_by == "model"
                    if blocked:
                        result["passed"] = (
                            result["passed"]
                            and all(
                                any(
                                    c.get("errorType") == "ReadTimeout" and c["model"] == model for c in calls
                                )
                                for model in blocked
                            )
                            and bool(calls)
                            and calls[-1]["model"] == settings.chat_fallback_models[len(blocked) - 1]
                        )
                except Exception as exc:
                    result = {
                        "case": name,
                        "passed": False,
                        "errorType": type(exc).__name__,
                        "errorMessage": str(exc).replace(active.api_key, "<redacted>")[:500],
                        "spec": spec.model_dump() if spec is not None else None,
                        "modelsAttempted": [c["model"] for c in calls],
                    }
                result["elapsedSeconds"] = round(perf_counter() - started, 3)
                artifact = store.add(
                    run_id, 10, "chart_selection_test", result, [source["artifactId"], *outputs]
                )
                store.step(
                    run_id,
                    10,
                    name,
                    status="succeeded" if result["passed"] else "failed",
                    duration_ms=result["elapsedSeconds"] * 1000,
                    inputs=[source["artifactId"]],
                    outputs=[*outputs, artifact["artifactId"]],
                )
                print(
                    json.dumps(
                        {k: v for k, v in result.items() if k not in {"option", "spec"}}, ensure_ascii=False
                    ),
                    flush=True,
                )
                return result

        selected = [item for item in CASES if args.case is None or item[0] == args.case]
        results = await asyncio.gather(
            *(case(f"{item[0]}_{index + 1}", *item[1:]) for index in range(args.repeat) for item in selected)
        )
        trend = CASES[0]
        if not args.skip_failover:
            results.append(await case("fallback_to_qwen_max", *trend[1:], blocked=(active.model_name,)))
            results.append(
                await case(
                    "fallback_to_deepseek",
                    *trend[1:],
                    blocked=(active.model_name, settings.chat_fallback_models[0]),
                )
            )
        passed = sum(bool(result["passed"]) for result in results)
        store.finish(run_id, 10, "succeeded" if passed == len(results) else "failed")
        print(
            json.dumps(
                {
                    "runId": run_id,
                    "passed": passed,
                    "total": len(results),
                    "activeModelUnchanged": active.model_name,
                }
            ),
            flush=True,
        )
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
