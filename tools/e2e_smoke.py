from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def _events(response: httpx.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    values: list[dict[str, Any]] = []
    event_name: str | None = None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            payload = json.loads(line.partition(":")[2].strip())
            payload["_sseEvent"] = event_name
            values.append(payload)
            event_name = None
    return values


def _create_analysis_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE IF EXISTS sales")
        connection.execute(
            "CREATE TABLE sales (id INTEGER PRIMARY KEY, month TEXT NOT NULL, revenue REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO sales(month, revenue) VALUES (?, ?)",
            [("2026-07", 120.5), ("2026-08", 151.0), ("2026-09", 173.25)],
        )


def run(
    base_url: str,
    analysis_db: Path,
    *,
    mysql_host: str | None = None,
    mysql_port: int = 3306,
    mysql_password: str = "root",
) -> dict[str, Any]:
    run_suffix = uuid4().hex[:8]
    if mysql_host is None:
        _create_analysis_database(analysis_db)
        agent_name = f"E2E Smoke Agent {run_suffix}"
        datasource_payload = {
            "name": f"E2E SQLite {run_suffix}",
            "type": "sqlite",
            "host": "localhost",
            "port": 0,
            "databaseName": str(analysis_db.resolve()),
            "username": "",
            "password": "",
            "status": "active",
        }
        table_name = "sales"
        expected_columns = 3
        query = "分析 sales 表各月 revenue"
        nl2sql_query = "列出 sales 表 revenue"
    else:
        agent_name = f"Compose E2E Agent {run_suffix}"
        datasource_payload = {
            "name": f"Compose E2E MySQL {run_suffix}",
            "type": "mysql",
            "host": mysql_host,
            "port": mysql_port,
            "databaseName": "product_db",
            "username": "root",
            "password": mysql_password,
            "status": "active",
        }
        table_name = "users"
        expected_columns = 4
        query = "列出 users 表中的用户"
        nl2sql_query = query

    # These smoke tests target a loopback/Compose-published endpoint.  Inheriting
    # the workstation proxy can route 127.0.0.1 through an HTTP proxy and turn a
    # healthy local 200 response into an empty 502 response.
    with httpx.Client(base_url=base_url, timeout=120, trust_env=False) as client:
        assert client.get("/echo/ok").json() == "ok"
        health = client.get("/health").json()
        assert health["management"]["ready"] is True

        agent = client.post(
            "/api/agent",
            json={
                "name": agent_name,
                "description": "Disposable end-to-end verification agent",
                "status": "draft",
                "humanReviewEnabled": True,
            },
        )
        agent.raise_for_status()
        agent_id = agent.json()["id"]

        source = client.post(
            "/api/datasource",
            json=datasource_payload,
        )
        source.raise_for_status()
        source_id = source.json()["id"]
        assert "connectionUrl" not in source.json()

        client.post(f"/api/agent/{agent_id}/datasources/{source_id}").raise_for_status()
        selected = client.post(
            f"/api/agent/{agent_id}/datasources/tables",
            json={"datasourceId": source_id, "tables": [table_name]},
        )
        selected.raise_for_status()
        initialized = client.post(f"/api/agent/{agent_id}/datasources/init")
        initialized.raise_for_status()
        assert initialized.json()["data"] == {
            "tableCount": 1,
            "columnCount": expected_columns,
        }

        # The management database is persistent across Compose runs.  A unique
        # conversation prevents previous turns/checkpoints from changing this
        # verification run's graph context.
        conversation_id = f"e2e-smoke-{run_suffix}"
        first = client.get(
            "/api/stream/search",
            params={
                "agentId": agent_id,
                "conversationId": conversation_id,
                "query": query,
                "humanFeedback": "true",
            },
        )
        first_events = _events(first)
        reviews = [
            item for item in first_events if item["eventType"] == "HUMAN_FEEDBACK_REQUIRED"
        ]
        assert reviews, json.dumps(first_events, ensure_ascii=False)
        review = reviews[0]
        assert first_events[-1]["_sseEvent"] == "complete"

        resumed = client.get(
            "/api/stream/search",
            params={
                "agentId": agent_id,
                "conversationId": conversation_id,
                "threadId": review["threadId"],
                "query": query,
                "humanFeedback": "true",
                "humanFeedbackContent": "Accept",
                "rejectedPlan": "false",
            },
        )
        resumed_events = _events(resumed)
        assert any(item["nodeName"] == "SQL_EXECUTE_NODE" for item in resumed_events)
        assert any(item["eventType"] == "FINAL_ANSWER" for item in resumed_events)
        assert resumed_events[-1]["_sseEvent"] == "complete"
        assert not any(item["error"] for item in resumed_events)

        nl2sql = client.get(
            "/api/stream/search",
            params={
                "agentId": agent_id,
                "query": nl2sql_query,
                "nl2sqlOnly": "true",
            },
        )
        nl2sql_events = _events(nl2sql)
        sql = next(
            item["text"]
            for item in nl2sql_events
            if item["nodeName"] == "SQL_GENERATE_NODE" and item["textType"] == "SQL"
        )
        assert sql.upper().startswith("SELECT ")

        return {
            "health": health["status"],
            "agentName": agent_name,
            "agentId": agent_id,
            "datasourceId": source_id,
            "schema": initialized.json()["data"],
            "hitlThreadId": review["threadId"],
            "resumedEventCount": len(resumed_events),
            "nl2sql": sql,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DataAgent HTTP/SSE/HITL smoke flow")
    parser.add_argument("--base-url", default="http://127.0.0.1:8065")
    parser.add_argument("--analysis-db", type=Path, default=Path("work/smoke/analysis.db"))
    parser.add_argument("--mysql-host")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-password", default="root")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.base_url,
                args.analysis_db,
                mysql_host=args.mysql_host,
                mysql_port=args.mysql_port,
                mysql_password=args.mysql_password,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
