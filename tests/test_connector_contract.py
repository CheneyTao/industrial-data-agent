from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import URL

from dataagent.connectors import (
    SUPPORTED_DATASOURCES,
    JdbcConnector,
    database_and_schema,
    datasource_type,
    dialect_name,
    sqlalchemy_url,
)
from dataagent.errors import ConfigurationError
from dataagent.models import Datasource


def _source(kind: str, *, database: str = "analytics", connection_url: str | None = None) -> Datasource:
    return Datasource(
        name=f"{kind} source",
        type=kind,
        host="db.internal",
        port=15432,
        database_name=database,
        schema_name=None,
        username="report user",
        password="p@ss/word",
        connection_url=connection_url,
        status="active",
    )


def test_upstream_public_connector_catalog_is_complete() -> None:
    assert [item["code"] for item in SUPPORTED_DATASOURCES] == [
        "mysql",
        "postgresql",
        "h2",
        "dameng",
        "sqlserver",
        "oracle",
        "hive",
    ]


@pytest.mark.parametrize(
    ("kind", "driver", "dialect"),
    [
        ("mysql", "mysql+pymysql", "mysql"),
        ("postgresql", "postgresql+psycopg", "postgres"),
        ("sqlserver", "mssql+pyodbc", "tsql"),
        ("oracle", "oracle+oracledb", "oracle"),
        ("hive", "hive", "hive"),
    ],
)
def test_sqlalchemy_connector_urls_and_sql_dialects(
    kind: str, driver: str, dialect: str
) -> None:
    source = _source(kind)
    value = sqlalchemy_url(source)

    assert isinstance(value, URL)
    assert value.drivername == driver
    assert value.host == "db.internal"
    assert value.port == 15432
    assert value.database == "analytics"
    assert value.username == "report user"
    assert value.password == "p@ss/word"
    assert dialect_name(source) == dialect


def test_legacy_database_schema_and_explicit_schema_are_compatible() -> None:
    legacy = _source("postgresql", database="warehouse|tenant_a")
    assert database_and_schema(legacy) == ("warehouse", "tenant_a")
    legacy.schema_name = "tenant_b"
    assert database_and_schema(legacy) == ("warehouse", "tenant_b")


def test_native_connection_url_overrides_generated_url_but_jdbc_does_not() -> None:
    native = "postgresql+psycopg://readonly:secret@host/db"
    assert sqlalchemy_url(_source("postgresql", connection_url=native)) == native
    jdbc_source = _source("mysql", connection_url="jdbc:mysql://legacy:3306/db")
    generated = sqlalchemy_url(jdbc_source)
    assert isinstance(generated, URL) and generated.host == "db.internal"


def test_h2_and_dameng_use_explicit_jdbc_driver_configuration(settings, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "jaydebeapi", SimpleNamespace(connect=lambda *_args: None))
    settings.jdbc_h2_jars = "drivers/h2.jar"
    settings.jdbc_dameng_jars = "drivers/dm.jar,drivers/helper.jar"

    h2 = JdbcConnector(_source("h2"), settings)
    assert h2._config() == (
        settings.jdbc_h2_driver_class,
        "jdbc:h2:tcp://db.internal:15432/analytics",
        ["drivers/h2.jar"],
    )
    dm = JdbcConnector(_source("dameng"), settings)
    assert dm._config() == (
        settings.jdbc_dameng_driver_class,
        "jdbc:dm://db.internal:15432/analytics",
        ["drivers/dm.jar", "drivers/helper.jar"],
    )


def test_aliases_and_unsupported_types_fail_closed() -> None:
    assert datasource_type("postgres") == "postgresql"
    assert datasource_type("MSSQL") == "sqlserver"
    with pytest.raises(ConfigurationError, match="Unsupported datasource type"):
        datasource_type("mongodb")
    with pytest.raises(ConfigurationError, match="JDBC adapter"):
        sqlalchemy_url(_source("h2"))


class _FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]], calls: list[tuple[str, object]]) -> None:
        self.rows = rows
        self.calls = calls

    def execute(self, sql: str, parameters=None) -> None:
        self.calls.append((sql, parameters))

    def fetchall(self):
        return list(self.rows)

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, rows: list[tuple[object, ...]], calls: list[tuple[str, object]]) -> None:
        self.rows = rows
        self.calls = calls

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.rows, self.calls)

    def close(self) -> None:
        return None


class _FakeJdbcDriver:
    def __init__(self, responses: list[list[tuple[object, ...]]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, object]] = []

    def connect(self, *_args) -> _FakeConnection:
        return _FakeConnection(self.responses.pop(0), self.calls)


async def test_h2_jdbc_metadata_preserves_schema_columns_identity_and_foreign_keys(
    settings, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "jaydebeapi", SimpleNamespace(connect=lambda *_args: None))
    settings.jdbc_h2_jars = "drivers/h2.jar"
    source = _source("h2", database="analytics|TENANT_A")
    connector = JdbcConnector(source, settings)
    driver = _FakeJdbcDriver(
        [
            [("ORDERS",), ("USERS",)],
            [
                ("ID", "order id", "BIGINT", "YES", "NO"),
                ("USER_ID", None, "BIGINT", "NO", "YES"),
            ],
            [("ORDERS", "USER_ID", "USERS", "ID")],
        ]
    )
    connector.driver = driver

    assert await connector.tables() == ["ORDERS", "USERS"]
    columns = await connector.columns("ORDERS")
    assert columns == [
        {
            "name": "ID",
            "description": "order id",
            "type": "BIGINT",
            "primaryKey": True,
            "nullable": False,
        },
        {
            "name": "USER_ID",
            "description": None,
            "type": "BIGINT",
            "primaryKey": False,
            "nullable": True,
        },
    ]
    assert await connector.foreign_keys("ORDERS") == [
        {
            "sourceTableName": "ORDERS",
            "sourceColumnName": "USER_ID",
            "targetTableName": "USERS",
            "targetColumnName": "ID",
            "relationType": "PHYSICAL",
        }
    ]
    assert driver.calls[0][1] == ["TENANT_A"]
    assert driver.calls[1][1] == ["TENANT_A", "ORDERS"]
    assert driver.calls[2][1] == ["TENANT_A", "ORDERS"]


async def test_dameng_jdbc_metadata_matches_upstream_contract(settings, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "jaydebeapi", SimpleNamespace(connect=lambda *_args: None))
    settings.jdbc_dameng_jars = "drivers/dm.jar"
    connector = JdbcConnector(_source("dameng"), settings)
    driver = _FakeJdbcDriver(
        [
            [("ID", "NUMBER", 22, "N"), ("NAME", "VARCHAR2", 100, "Y")],
            [("ORDERS", "USER_ID", "FK_ORDERS", "SYSDBA", "PK_USERS")],
        ]
    )
    connector.driver = driver

    columns = await connector.columns("ORDERS")
    assert columns[0]["nullable"] is False
    assert columns[1]["nullable"] is True
    assert all(column["primaryKey"] is False for column in columns)
    assert await connector.foreign_keys("ORDERS") == [
        {
            "sourceTableName": "ORDERS",
            "sourceColumnName": "USER_ID",
            "targetTableName": "",
            "targetColumnName": "",
            "relationType": "PHYSICAL",
        }
    ]
