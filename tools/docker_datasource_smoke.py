from __future__ import annotations

import argparse
import json
from typing import Any

import httpx


def _source_payload(
    *, name: str, kind: str, host: str, port: int, database: str, username: str, password: str
) -> dict[str, Any]:
    return {
        "name": name,
        "type": kind,
        "host": host,
        "port": port,
        "databaseName": database,
        "username": username,
        "password": password,
        "status": "active",
    }


def run(
    base_url: str,
    *,
    mysql_host: str = "127.0.0.1",
    mysql_port: int = 13306,
    mysql_password: str = "root",
    postgres_host: str = "127.0.0.1",
    postgres_port: int = 15432,
    postgres_password: str = "postgres",
) -> dict[str, Any]:
    created: list[int] = []
    result: dict[str, Any] = {}
    with httpx.Client(base_url=base_url, timeout=60, trust_env=False) as client:
        try:
            definitions = (
                (
                    "mysql",
                    _source_payload(
                        name="Docker MySQL smoke",
                        kind="mysql",
                        host=mysql_host,
                        port=mysql_port,
                        database="product_db",
                        username="root",
                        password=mysql_password,
                    ),
                    "users",
                    4,
                ),
                (
                    "postgresql",
                    _source_payload(
                        name="Docker PostgreSQL smoke",
                        kind="postgresql",
                        host=postgres_host,
                        port=postgres_port,
                        database="china_population_db",
                        username="postgres",
                        password=postgres_password,
                    ),
                    "population_total",
                    8,
                ),
            )
            for label, payload, expected_table, expected_columns in definitions:
                response = client.post("/api/datasource", json=payload)
                response.raise_for_status()
                source = response.json()
                source_id = int(source["id"])
                created.append(source_id)
                assert source["password"].startswith("****")
                assert "connectionUrl" not in source

                tested = client.post(f"/api/datasource/{source_id}/test")
                tested.raise_for_status()
                assert tested.json()["success"] is True, tested.text

                tables_response = client.get(f"/api/datasource/{source_id}/tables")
                tables_response.raise_for_status()
                tables = tables_response.json()
                table_names = {
                    str(item.get("name") or item.get("tableName"))
                    if isinstance(item, dict)
                    else str(item)
                    for item in tables
                }
                assert expected_table in table_names, table_names

                columns_response = client.get(
                    f"/api/datasource/{source_id}/tables/{expected_table}/columns"
                )
                columns_response.raise_for_status()
                columns = columns_response.json()["data"]
                assert len(columns) == expected_columns, columns
                result[label] = {
                    "connected": True,
                    "tableCount": len(table_names),
                    "verifiedTable": expected_table,
                    "columnCount": len(columns),
                }
        finally:
            for source_id in reversed(created):
                response = client.delete(f"/api/datasource/{source_id}")
                response.raise_for_status()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Docker demo datasources through FastAPI")
    parser.add_argument("--base-url", default="http://127.0.0.1:8065")
    parser.add_argument("--mysql-host", default="127.0.0.1")
    parser.add_argument("--mysql-port", type=int, default=13306)
    parser.add_argument("--mysql-password", default="root")
    parser.add_argument("--postgres-host", default="127.0.0.1")
    parser.add_argument("--postgres-port", type=int, default=15432)
    parser.add_argument("--postgres-password", default="postgres")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.base_url,
                mysql_host=args.mysql_host,
                mysql_port=args.mysql_port,
                mysql_password=args.mysql_password,
                postgres_host=args.postgres_host,
                postgres_port=args.postgres_port,
                postgres_password=args.postgres_password,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
