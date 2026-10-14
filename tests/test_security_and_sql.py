from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from dataagent.config import Settings
from dataagent.connectors import SqlAlchemyConnector, limited_sql, validate_read_only_sql
from dataagent.errors import UnsafeSqlError
from dataagent.models import Datasource
from dataagent.security import create_api_key, hash_api_key, masked_api_key, verify_api_key


def _sqlite_source(path: Path) -> Datasource:
    return Datasource(
        name="analytics",
        type="sqlite",
        host="localhost",
        port=0,
        database_name=str(path),
        username="",
        password="",
        connection_url=f"sqlite:///{path.as_posix()}",
    )


def test_api_key_format_hash_mask_and_legacy_compatibility() -> None:
    plain = create_api_key()
    assert plain.startswith("sk-")
    assert len(plain) == 35
    assert plain[3:].isalnum()

    encoded = hash_api_key(plain)
    assert plain not in encoded
    assert encoded.startswith("{bcrypt}$2")
    assert encoded.endswith("::" + plain[-4:])
    assert verify_api_key(plain, encoded)
    assert not verify_api_key(plain + "x", encoded)
    assert masked_api_key(encoded) == "****" + plain[-4:]

    assert verify_api_key(plain, plain)
    sha = hashlib.sha256(plain.encode()).hexdigest() + "::" + plain[-4:]
    assert verify_api_key(plain, sha)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sales VALUES (1)",
        "UPDATE sales SET amount = 0",
        "DELETE FROM sales",
        "DROP TABLE sales",
        "SELECT 1; SELECT 2",
        "PRAGMA table_info(sales)",
        "SELECT * INTO new_table FROM sales",
        "SELECT * FROM sales FOR UPDATE",
        "EXPLAIN ANALYZE SELECT * FROM sales",
    ],
)
def test_sql_safety_rejects_mutation_and_multiple_statements(sql: str) -> None:
    with pytest.raises(UnsafeSqlError):
        validate_read_only_sql(sql)


def test_sql_safety_allows_select_cte_and_preserves_existing_limit(tmp_path: Path) -> None:
    source = _sqlite_source(tmp_path / "analysis.db")
    assert validate_read_only_sql("WITH x AS (SELECT 1 AS n) SELECT n FROM x")
    assert "LIMIT 2" in limited_sql("SELECT * FROM sales", source, 2).upper()
    assert "LIMIT 1" in limited_sql("SELECT * FROM sales LIMIT 1", source, 2).upper()
    assert "LIMIT 2" in limited_sql("SELECT * FROM sales LIMIT 999999", source, 2).upper()
    assert "LIMIT 2" in limited_sql("SELECT * FROM sales LIMIT -1", source, 2).upper()


async def test_sqlite_connector_introspection_and_result_bound(tmp_path: Path) -> None:
    path = tmp_path / "analysis.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, amount REAL NOT NULL)")
        connection.executemany("INSERT INTO sales(amount) VALUES (?)", [(10,), (20,), (30,)])

    settings = Settings(
        _env_file=None,
        model_mode="mock",
        management_database_url="sqlite+aiosqlite:///:memory:",
        sql_max_rows=2,
    )
    connector = SqlAlchemyConnector(_sqlite_source(path), settings)
    assert await connector.test()
    assert await connector.tables() == ["sales"]
    columns = await connector.columns("sales")
    assert [column["name"] for column in columns] == ["id", "amount"]
    assert columns[0]["primaryKey"] is True

    result = await connector.execute("SELECT id, amount FROM sales ORDER BY id")
    assert result.columns == ["id", "amount"]
    assert result.rows == [[1, 10.0], [2, 20.0]]
    assert result.row_count == 2
    assert result.truncated is True
