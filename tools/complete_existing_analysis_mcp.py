"""Continue an existing NL2SQL result through modular MCP, without requerying data."""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agent-id", required=True, type=int)
    parser.add_argument("--x", required=True)
    parser.add_argument("--y", required=True)
    parser.add_argument("--type", choices=["column", "bar", "line", "scatter"], default="bar")
    parser.add_argument("--title", default="查询结果对比")
    args = parser.parse_args()
    async with httpx2.AsyncClient(trust_env=False, timeout=600) as http:
        async with Client(
            streamable_http_client("http://127.0.0.1:8066/mcp", http_client=http)
        ) as client:

            async def call(name, **parameters):
                response = await client.call_tool(
                    name, {"agentId": args.agent_id, "runId": args.run_id, **parameters}
                )
                if response.is_error:
                    raise RuntimeError(f"MCP {name} failed: {response.content}")
                return json.loads(response.content[0].text)

            trace = await call("get_run_trace")
            result = next(a for a in reversed(trace["artifacts"]) if a["kind"] == "query_result")
            chart = await call(
                "create_chart",
                resultArtifactId=result["artifactId"],
                spec={
                    "type": args.type,
                    "title": args.title,
                    "x": args.x,
                    "y": [args.y],
                },
            )
            await call(
                "analyze_result",
                resultArtifactId=result["artifactId"],
                chartArtifactId=chart["artifactId"],
            )
            print(json.dumps({"phase": "chart_and_analysis_saved", "runId": args.run_id}), flush=True)
            report = await call(
                "generate_report",
                focus=(
                    "回答原始用户问题；说明这是复用已保存的真实查询结果，通过 MCP 补齐图表和分析，"
                    "没有重新查询数据库。清晰给出各型号 OEE、差值、图表解读、均值口径与待验证改善建议。"
                    "不声称证明了因果关系、统计显著性或真实生产收益。"
                ),
            )
            trace = await call("get_run_trace")
            print(
                json.dumps(
                    {
                        "runId": args.run_id,
                        "status": trace["status"],
                        "reportArtifactId": report["artifactId"],
                        "artifactCount": len(trace["artifacts"]),
                        "queryReexecuted": False,
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
