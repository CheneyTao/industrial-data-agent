"""Live MCP modular rendering check; this reference SQL is NOT an NL2SQL test."""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

AGENT_ID = 10
REFERENCE_SQL = (
    "SELECT operation_month, model_name, avg_oee_pct "
    "FROM v_monthly_operations_kpi WHERE operation_month LIKE '2015%' "
    "ORDER BY operation_month, model_name"
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="Resume a previous verification after a client interruption")
    args = parser.parse_args()
    # Only local requests bypass proxy; no system proxy setting is modified.
    async with httpx2.AsyncClient(trust_env=False, timeout=300) as http:
        async with Client(streamable_http_client("http://127.0.0.1:8066/mcp", http_client=http)) as client:

            async def call(name, **args):
                response = await client.call_tool(name, {"agentId": AGENT_ID, **args})
                if response.is_error:
                    raise RuntimeError(f"MCP verification failed at {name}: {response.content}")
                return json.loads(response.content[0].text)

            run = (
                await call("get_run_trace", runId=args.run_id)
                if args.run_id
                else await call(
                    "begin_analysis",
                    question=(
                        "独立 MCP 验收：用参考 SQL 查询 2015 月度 OEE，生成同源热力图与统计；"
                        "非自然语言生成 SQL 测试。"
                    ),
                )
            )
            run_id = run["runId"]
            await call("get_schema", runId=run_id)
            result = await call("execute_query", runId=run_id, sql=REFERENCE_SQL)
            result_id = result["provenance"]["artifactId"]
            chart = await call(
                "create_chart",
                runId=run_id,
                resultArtifactId=result_id,
                spec={
                    "type": "heatmap",
                    "title": "2015 年各型号月度 OEE（合成演示数据，%）",
                    "x": "operation_month",
                    "category": "model_name",
                    "y": ["avg_oee_pct"],
                },
            )
            await call(
                "analyze_result",
                runId=run_id,
                resultArtifactId=result_id,
                chartArtifactId=chart["artifactId"],
            )
            trace = await call("finish_analysis", runId=run_id)
            assert result["rowCount"] == 48 and not result["truncated"]
            assert len(chart["content"]["option"]["series"][0]["data"]) == 48
            print(
                json.dumps(
                    {
                        "runId": run_id,
                        "status": trace["status"],
                        "rows": result["rowCount"],
                        "chartType": "heatmap",
                        "artifactCount": len(trace["artifacts"]),
                        "nl2sqlTest": False,
                    }
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
