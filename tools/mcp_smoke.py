from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _content_text(result: Any) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content if hasattr(item, "text"))


async def run(
    url: str,
    agent_id: str,
    *,
    expected_agent: str = "E2E Smoke Agent",
    query: str = "列出 sales 表 revenue",
) -> dict[str, Any]:
    timeout = httpx2.Timeout(30.0, read=300.0)
    # MCP is published on loopback for this verifier.  Do not inherit a host
    # proxy: it must never intercept the local Streamable HTTP session.
    async with httpx2.AsyncClient(timeout=timeout, trust_env=False) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read, write = streams[:2]
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                assert names == ["listAgentsToolCallback", "nl2SqlToolCallback"]

                listed = await session.call_tool("listAgentsToolCallback", arguments={})
                assert not listed.is_error, _content_text(listed)
                listed_text = _content_text(listed)
                assert expected_agent in listed_text

                converted = await session.call_tool(
                    "nl2SqlToolCallback",
                    arguments={"naturalQuery": query, "agentId": agent_id},
                )
                assert not converted.is_error, _content_text(converted)
                sql = _content_text(converted).strip().strip('"')
                assert sql.upper().startswith("SELECT "), sql
                return {"tools": names, "listed": True, "nl2sql": sql}


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify DataAgent MCP over Streamable HTTP")
    parser.add_argument("--url", default="http://127.0.0.1:8066/mcp")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--expected-agent", default="E2E Smoke Agent")
    parser.add_argument("--query", default="列出 sales 表 revenue")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                run(
                    args.url,
                    args.agent_id,
                    expected_agent=args.expected_agent,
                    query=args.query,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
