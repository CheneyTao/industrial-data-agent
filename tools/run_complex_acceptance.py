"""Run the original complex question through the public MCP entry point; no reference SQL."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

QUESTION = (
    "请做一份 2015 年第四季度设备运行与维护复盘，并与第三季度比较。"
    "比较各型号的平均 OEE、停机总时长、故障次数和维护总成本，说明变化幅度；"
    "找出第四季度停机时长最高的 5 台设备，核对它们的故障和工单情况；"
    "展示 7—12 月按型号的 OEE 趋势，给出有证据的维修优先级建议。"
    "请区分公开样例与合成数据，明确平均值和变化率口径，避免多表关联重复计数，"
    "保留 SQL、图表与结论的来源；证据不足的原因不要当作事实。"
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-report", help="Resume only the report on an existing run's saved evidence")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, content: object) -> None:
        (args.output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    start = datetime.now(UTC)
    save(
        "request.json",
        {
            "startedAt": start.isoformat(),
            "agentId": 10,
            "question": QUESTION,
            "entrypoint": "MCP generate_report" if args.resume_report else "MCP analyze_request",
            "resumedRunId": args.resume_report,
            "referenceSqlProvided": False,
        },
    )
    print(json.dumps({"phase": "started", "at": start.isoformat()}), flush=True)
    async with httpx2.AsyncClient(trust_env=False, timeout=1800) as http:
        async with Client(
            streamable_http_client("http://127.0.0.1:8066/mcp", http_client=http), read_timeout_seconds=1800
        ) as client:
            discovered = await client.list_tools()
            save("discovered_tools.json", [tool.name for tool in discovered.tools])
            response = await client.call_tool(
                "generate_report" if args.resume_report else "analyze_request",
                {"agentId": 10, "runId": args.resume_report}
                if args.resume_report
                else {"agentId": 10, "question": QUESTION},
                read_timeout_seconds=1800,
            )
            texts = [item.text for item in response.content if hasattr(item, "text")]
            if response.is_error:
                result = {"status": "failed", "message": texts}
            else:
                result = json.loads(texts[0]) if texts else {}
            if args.resume_report:
                trace = await client.call_tool("get_run_trace", {"agentId": 10, "runId": args.resume_report})
                manifest = json.loads(trace.content[0].text)
                result.update(runId=args.resume_report, status=manifest["status"])
            elapsed = (datetime.now(UTC) - start).total_seconds()
            save(
                "mcp_response.json",
                {"elapsedSeconds": elapsed, "isError": response.is_error, "result": result},
            )
            print(
                json.dumps(
                    {
                        "phase": "finished",
                        "elapsedSeconds": elapsed,
                        "runId": result.get("runId"),
                        "status": result.get("status"),
                        "isError": response.is_error,
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
