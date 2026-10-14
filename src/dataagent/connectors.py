from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.config import Settings, get_settings
from dataagent.errors import ConfigurationError, DataAgentError, NotFoundError, UnsafeSqlError
from dataagent.models import AgentDatasource, AgentDatasourceTable, Datasource
from dataagent.query_execution import run_read_operation
from dataagent.schemas import ColumnDTO, SchemaDTO, TableDTO, normalize_identifier

SUPPORTED_DATASOURCES: list[dict[str, str]] = [
    {
        "code": "mysql",
        "typeName": "MySQL",
        "dialect": "MySQL",
        "protocol": "jdbc:mysql",
        "displayName": "MySQL",
    },
    {
        "code": "postgresql",
        "typeName": "PostgreSQL",
        "dialect": "PostgreSQL",
        "protocol": "jdbc:postgresql",
        "displayName": "PostgreSQL",
    },
    {"code": "h2", "typeName": "H2", "dialect": "H2", "protocol": "jdbc:h2", "displayName": "H2"},
    {"code": "dameng", "typeName": "Dameng", "dialect": "DM", "protocol": "jdbc:dm", "displayName": "DM"},
    {
        "code": "sqlserver",
        "typeName": "SQL Server",
        "dialect": "SQLServer",
        "protocol": "jdbc:sqlserver",
        "displayName": "SQLServer",
    },
    {
        "code": "oracle",
        "typeName": "Oracle",
        "dialect": "Oracle",
        "protocol": "jdbc:oracle",
        "displayName": "Oracle",
    },
    {"code": "hive", "typeName": "Hive", "dialect": "Hive", "protocol": "jdbc:hive2", "displayName": "Hive"},
]


TYPE_ALIASES = {
    "mysql": "mysql",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "h2": "h2",
    "dameng": "dameng",
    "dm": "dameng",
    "sql_server": "sqlserver",
    "sql-server": "sqlserver",
    "sqlserver": "sqlserver",
    "mssql": "sqlserver",
    "oracle": "oracle",
    "hive": "hive",
    "sqlite": "sqlite",
}


def datasource_type(value: str) -> str:
    normalized = value.strip().lower()
    try:
        return TYPE_ALIASES[normalized]
    except KeyError as exc:
        raise ConfigurationError(f"Unsupported datasource type: {value}") from exc


def database_and_schema(source: Datasource) -> tuple[str, str | None]:
    database = source.database_name
    schema = source.schema_name
    if "|" in database:
        legacy_database, legacy_schema = database.split("|", 1)
        database = legacy_database
        schema = schema or legacy_schema
    return database, schema


def sqlalchemy_url(source: Datasource) -> str | URL:
    if source.connection_url and not source.connection_url.lower().startswith("jdbc:"):
        return source.connection_url
    kind = datasource_type(source.type)
    database, _ = database_and_schema(source)
    if kind == "sqlite":
        if source.connection_url:
            return source.connection_url
        return f"sqlite:///{database}"
    if kind == "mysql":
        driver = "mysql+pymysql"
        query = {"charset": "utf8mb4"}
    elif kind == "postgresql":
        driver, query = "postgresql+psycopg", {}
    elif kind == "oracle":
        driver, query = "oracle+oracledb", {}
    elif kind == "hive":
        driver, query = "hive", {"auth": "CUSTOM"}
    elif kind == "sqlserver":
        driver, query = (
            "mssql+pyodbc",
            {"driver": "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes"},
        )
    else:
        raise ConfigurationError(f"{kind} uses the JDBC adapter, not SQLAlchemy")
    return URL.create(
        drivername=driver,
        username=source.username,
        password=source.password,
        host=source.host,
        port=source.port,
        database=database,
        query=query,
    )


def dialect_name(source: Datasource) -> str:
    kind = datasource_type(source.type)
    return {
        "mysql": "mysql",
        "postgresql": "postgres",
        "sqlite": "sqlite",
        "h2": "postgres",
        "dameng": "oracle",
        "sqlserver": "tsql",
        "oracle": "oracle",
        "hive": "hive",
    }[kind]


def validate_read_only_sql(sql: str, source: Datasource | None = None) -> str:
    cleaned = sql.strip()
    cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned:
        raise UnsafeSqlError("SQL must not be empty")
    try:
        expressions = sqlglot.parse(cleaned, read=dialect_name(source) if source else None)
    except Exception as exc:
        raise UnsafeSqlError(f"SQL parse failed: {exc}") from exc
    if len(expressions) != 1:
        raise UnsafeSqlError("Only one SQL statement is allowed")
    expression = expressions[0]
    forbidden = {
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Grant",
        "Revoke",
        "Merge",
        "Command",
        "Copy",
        "LoadData",
        "Transaction",
        "Into",
        "Lock",
    }
    for node in expression.walk():
        if type(node).__name__ in forbidden:
            raise UnsafeSqlError(f"Read-only SQL required; {type(node).__name__} is not allowed")
    # SELECT, UNION and CTE-rooted queries are allowed; executable commands are not.
    if type(expression).__name__ not in {"Select", "Union", "Intersect", "Except"}:
        raise UnsafeSqlError("Only SELECT/CTE queries are allowed")
    return cleaned.rstrip(";").strip()


def limited_sql(sql: str, source: Datasource, limit: int) -> str:
    expression = sqlglot.parse_one(sql, read=dialect_name(source))
    existing = expression.args.get("limit")
    value = existing.expression if existing is not None else None
    if (
        not isinstance(value, sqlglot.exp.Literal)
        or not value.is_int
        or not 0 <= int(value.this) <= limit
        or existing.args.get("limit_options")
    ):
        expression = expression.limit(limit)
    return expression.sql(dialect=dialect_name(source))


@dataclass(slots=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        data = [dict(zip(self.columns, row, strict=True)) for row in self.rows]
        return {
            "column": self.columns,
            "data": data,
            "errorMsg": None,
            "rowCount": self.row_count,
            "truncated": self.truncated,
        }


class SqlAlchemyConnector:
    def __init__(self, source: Datasource, settings: Settings) -> None:
        self.source = source
        self.settings = settings

    def _engine(self) -> Engine:
        return create_engine(sqlalchemy_url(self.source), pool_pre_ping=True, future=True)

    async def test(self) -> bool:
        def operation() -> bool:
            engine = self._engine()
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return True
            finally:
                engine.dispose()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def tables(self) -> list[str]:
        def operation() -> list[str]:
            engine = self._engine()
            try:
                _, schema = database_and_schema(self.source)
                inspector = inspect(engine)
                return sorted(
                    set(inspector.get_table_names(schema=schema) + inspector.get_view_names(schema=schema))
                )
            finally:
                engine.dispose()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def columns(self, table_name: str) -> list[dict[str, Any]]:
        normalize_identifier(table_name)

        def operation() -> list[dict[str, Any]]:
            engine = self._engine()
            try:
                _, schema = database_and_schema(self.source)
                inspector = inspect(engine)
                primary = set(
                    (inspector.get_pk_constraint(table_name, schema=schema) or {}).get("constrained_columns")
                    or []
                )
                return [
                    {
                        "name": column["name"],
                        "type": str(column.get("type", "")),
                        "nullable": bool(column.get("nullable", True)),
                        "defaultValue": str(column.get("default"))
                        if column.get("default") is not None
                        else None,
                        "description": column.get("comment"),
                        "primaryKey": column["name"] in primary,
                    }
                    for column in inspector.get_columns(table_name, schema=schema)
                ]
            finally:
                engine.dispose()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def foreign_keys(self, table_name: str) -> list[dict[str, Any]]:
        normalize_identifier(table_name)

        def operation() -> list[dict[str, Any]]:
            engine = self._engine()
            try:
                _, schema = database_and_schema(self.source)
                inspector = inspect(engine)
                result = []
                for foreign_key in inspector.get_foreign_keys(table_name, schema=schema):
                    for source_column, target_column in zip(
                        foreign_key.get("constrained_columns", []),
                        foreign_key.get("referred_columns", []),
                        strict=False,
                    ):
                        result.append(
                            {
                                "sourceTableName": table_name,
                                "sourceColumnName": source_column,
                                "targetTableName": foreign_key.get("referred_table"),
                                "targetColumnName": target_column,
                                "relationType": "PHYSICAL",
                            }
                        )
                return result
            finally:
                engine.dispose()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def sample_values(
        self, table_name: str, columns: list[str], *, limit: int = 5
    ) -> dict[str, list[str]]:
        normalize_identifier(table_name)
        for column in columns:
            normalize_identifier(column)
        if not columns:
            return {}

        def operation() -> dict[str, list[str]]:
            engine = self._engine()
            try:
                _, schema = database_and_schema(self.source)
                metadata = MetaData()
                reflected = Table(table_name, metadata, schema=schema, autoload_with=engine)
                selected = [reflected.c[column] for column in columns]
                with engine.connect() as connection:
                    rows = connection.execute(select(*selected).limit(limit)).fetchall()
                return _sample_values(columns, rows)
            finally:
                engine.dispose()

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds
            )
        except Exception:
            # Sample values enrich prompts but must never make schema initialization fail.
            return {}

    async def execute(self, sql: str) -> QueryResult:
        safe = validate_read_only_sql(sql, self.source)
        bounded = limited_sql(safe, self.source, self.settings.sql_max_rows + 1)

        def operation(connection: Any) -> QueryResult:
            result = connection.execute(text(bounded))
            columns = list(result.keys())
            raw_rows = result.fetchmany(self.settings.sql_max_rows + 1)
            truncated = len(raw_rows) > self.settings.sql_max_rows
            raw_rows = raw_rows[: self.settings.sql_max_rows]
            rows = [[_json_scalar(value) for value in row] for row in raw_rows]
            return QueryResult(columns, rows, len(rows), truncated)

        engine = self._engine()
        try:
            return await run_read_operation(engine, operation, self.settings.sql_query_timeout_seconds)
        except TimeoutError as exc:
            raise DataAgentError(
                f"SQL execution timed out after {self.settings.sql_query_timeout_seconds}s"
            ) from exc
        except SQLAlchemyError as exc:
            raise DataAgentError(f"SQL execution failed: {exc}") from exc
        finally:
            await asyncio.to_thread(engine.dispose)


class JdbcConnector:
    def __init__(self, source: Datasource, settings: Settings) -> None:
        self.source = source
        self.settings = settings
        try:
            import jaydebeapi
        except ImportError as exc:
            raise ConfigurationError("Install the 'connectors' extra to use H2 or Dameng") from exc
        self.driver = jaydebeapi

    def _config(self) -> tuple[str, str, list[str]]:
        kind = datasource_type(self.source.type)
        if kind == "h2":
            driver_class, jars = self.settings.jdbc_h2_driver_class, self.settings.jdbc_h2_jars
            database, _ = database_and_schema(self.source)
            url = (
                self.source.connection_url
                or f"jdbc:h2:tcp://{self.source.host}:{self.source.port}/{database}"
            )
        else:
            driver_class, jars = self.settings.jdbc_dameng_driver_class, self.settings.jdbc_dameng_jars
            url = (
                self.source.connection_url
                or f"jdbc:dm://{self.source.host}:{self.source.port}/{database_and_schema(self.source)[0]}"
            )
        if not jars:
            raise ConfigurationError(f"JDBC jar path is required for {kind}")
        return driver_class, url, [item.strip() for item in jars.split(",") if item.strip()]

    def _connect(self) -> Any:
        driver_class, url, jars = self._config()
        return self.driver.connect(driver_class, url, [self.source.username, self.source.password], jars)

    async def test(self) -> bool:
        def operation() -> bool:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
                return True
            finally:
                connection.close()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def tables(self) -> list[str]:
        def operation() -> list[str]:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                kind = datasource_type(self.source.type)
                if kind == "h2":
                    _, configured_schema = database_and_schema(self.source)
                    cursor.execute(
                        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = ? ORDER BY TABLE_NAME",
                        [configured_schema or "PUBLIC"],
                    )
                else:
                    cursor.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
                values = sorted({str(row[0]) for row in cursor.fetchall()})
                cursor.close()
                return values
            finally:
                connection.close()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def columns(self, table_name: str) -> list[dict[str, Any]]:
        normalize_identifier(table_name)

        def operation() -> list[dict[str, Any]]:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                kind = datasource_type(self.source.type)
                if kind == "h2":
                    _, configured_schema = database_and_schema(self.source)
                    cursor.execute(
                        "SELECT COLUMN_NAME, REMARKS, DATA_TYPE, IS_IDENTITY, IS_NULLABLE "
                        "FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
                        [configured_schema or "PUBLIC", table_name],
                    )
                    result = [
                        {
                            "name": str(row[0]),
                            "description": None if row[1] is None else str(row[1]),
                            "type": str(row[2]),
                            # H2JdbcDdl treats identity columns as primary, so retain
                            # that observable upstream behavior.
                            "primaryKey": str(row[3]).upper() in {"TRUE", "YES", "1"},
                            "nullable": str(row[4]).upper() != "NO",
                        }
                        for row in cursor.fetchall()
                    ]
                else:
                    cursor.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE "
                        "FROM USER_TAB_COLUMNS WHERE TABLE_NAME = ? ORDER BY COLUMN_ID",
                        [table_name],
                    )
                    result = [
                        {
                            "name": str(row[0]),
                            "description": "",
                            "type": str(row[1]),
                            "primaryKey": False,
                            "nullable": str(row[3]).upper() != "N",
                        }
                        for row in cursor.fetchall()
                    ]
                cursor.close()
                return result
            finally:
                connection.close()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def foreign_keys(self, table_name: str) -> list[dict[str, Any]]:
        normalize_identifier(table_name)

        def operation() -> list[dict[str, Any]]:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                kind = datasource_type(self.source.type)
                if kind == "h2":
                    _, configured_schema = database_and_schema(self.source)
                    cursor.execute(
                        "SELECT kc.TABLE_NAME, kc.COLUMN_NAME, kc2.TABLE_NAME, kc2.COLUMN_NAME "
                        "FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc "
                        "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kc "
                        "ON rc.CONSTRAINT_CATALOG = kc.CONSTRAINT_CATALOG "
                        "AND rc.CONSTRAINT_SCHEMA = kc.CONSTRAINT_SCHEMA "
                        "AND rc.CONSTRAINT_NAME = kc.CONSTRAINT_NAME "
                        "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kc2 "
                        "ON rc.UNIQUE_CONSTRAINT_CATALOG = kc2.CONSTRAINT_CATALOG "
                        "AND rc.UNIQUE_CONSTRAINT_SCHEMA = kc2.CONSTRAINT_SCHEMA "
                        "AND rc.UNIQUE_CONSTRAINT_NAME = kc2.CONSTRAINT_NAME "
                        "AND kc.POSITION_IN_UNIQUE_CONSTRAINT = kc2.ORDINAL_POSITION "
                        "WHERE kc.CONSTRAINT_SCHEMA = ? AND kc.TABLE_NAME = ? "
                        "ORDER BY kc.CONSTRAINT_NAME, kc.ORDINAL_POSITION",
                        [configured_schema or "PUBLIC", table_name],
                    )
                    rows = cursor.fetchall()
                    result = [
                        {
                            "sourceTableName": str(row[0]),
                            "sourceColumnName": str(row[1]),
                            "targetTableName": str(row[2]),
                            "targetColumnName": str(row[3]),
                            "relationType": "PHYSICAL",
                        }
                        for row in rows
                    ]
                else:
                    # DamengJdbcDdl exposes the imported-key source and leaves the
                    # referenced table/column blank; keep that API contract exactly.
                    cursor.execute(
                        "SELECT uc.TABLE_NAME, ucc.COLUMN_NAME, uc.CONSTRAINT_NAME, "
                        "uc.R_OWNER, uc.R_CONSTRAINT_NAME "
                        "FROM USER_CONSTRAINTS uc "
                        "JOIN USER_CONS_COLUMNS ucc "
                        "ON uc.CONSTRAINT_NAME = ucc.CONSTRAINT_NAME "
                        "WHERE uc.CONSTRAINT_TYPE = 'R' AND uc.TABLE_NAME = ? "
                        "ORDER BY uc.CONSTRAINT_NAME, ucc.POSITION",
                        [table_name],
                    )
                    result = [
                        {
                            "sourceTableName": str(row[0]),
                            "sourceColumnName": str(row[1]),
                            "targetTableName": "",
                            "targetColumnName": "",
                            "relationType": "PHYSICAL",
                        }
                        for row in cursor.fetchall()
                    ]
                cursor.close()
                return result
            finally:
                connection.close()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)

    async def sample_values(
        self, table_name: str, columns: list[str], *, limit: int = 5
    ) -> dict[str, list[str]]:
        normalize_identifier(table_name)
        for column in columns:
            normalize_identifier(column)
        if not columns:
            return {}
        sql = limited_sql(f"SELECT {', '.join(columns)} FROM {table_name}", self.source, limit)

        def operation() -> dict[str, list[str]]:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                cursor.execute(sql)
                rows = cursor.fetchmany(limit)
                cursor.close()
                return _sample_values(columns, rows)
            finally:
                connection.close()

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds
            )
        except Exception:
            return {}

    async def execute(self, sql: str) -> QueryResult:
        safe = validate_read_only_sql(sql, self.source)
        bounded = limited_sql(safe, self.source, self.settings.sql_max_rows + 1)

        def operation() -> QueryResult:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                cursor.execute(bounded)
                columns = [item[0] for item in cursor.description]
                raw_rows = cursor.fetchmany(self.settings.sql_max_rows + 1)
                cursor.close()
                truncated = len(raw_rows) > self.settings.sql_max_rows
                rows = [
                    [_json_scalar(value) for value in row] for row in raw_rows[: self.settings.sql_max_rows]
                ]
                return QueryResult(columns, rows, len(rows), truncated)
            finally:
                connection.close()

        return await asyncio.wait_for(asyncio.to_thread(operation), self.settings.sql_query_timeout_seconds)


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _sample_values(columns: list[str], rows: list[Any]) -> dict[str, list[str]]:
    """Match the Java schema sampler: first five rows, max three unique short values."""

    values: dict[str, list[str]] = {column: [] for column in columns}
    for row in rows:
        for index, column in enumerate(columns):
            value = row[index]
            if value is None:
                continue
            rendered = str(_json_scalar(value))
            if len(rendered) <= 100 and rendered not in values[column] and len(values[column]) < 3:
                values[column].append(rendered)
    return values


class ConnectorService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def for_source(self, source: Datasource) -> SqlAlchemyConnector | JdbcConnector:
        if datasource_type(source.type) in {"h2", "dameng"}:
            return JdbcConnector(source, self.settings)
        return SqlAlchemyConnector(source, self.settings)

    async def selected_tables(self, session: AsyncSession, agent_id: int) -> list[str]:
        result = await session.scalars(
            select(AgentDatasourceTable.table_name)
            .join(AgentDatasource)
            .where(AgentDatasource.agent_id == agent_id, AgentDatasource.is_active == 1)
        )
        return sorted(set(result))

    async def active_source(self, session: AsyncSession, agent_id: int) -> Datasource:
        from sqlalchemy import select

        result = await session.execute(
            select(Datasource)
            .join(AgentDatasource, AgentDatasource.datasource_id == Datasource.id)
            .where(AgentDatasource.agent_id == agent_id, AgentDatasource.is_active == 1)
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise NotFoundError(f"Agent {agent_id} has no active datasource")
        return source

    async def build_schema(self, session: AsyncSession, agent_id: int, tables: list[str]) -> SchemaDTO:
        source = await self.active_source(session, agent_id)
        connector = self.for_source(source)
        table_models: list[TableDTO] = []
        foreign_keys: list[str] = []
        selected_tables = {table.casefold() for table in tables}
        for table_name in tables:
            columns = await connector.columns(table_name)
            samples = await connector.sample_values(
                table_name, [str(item["name"]) for item in columns], limit=5
            )
            table_models.append(
                TableDTO(
                    name=table_name,
                    column=[
                        ColumnDTO(
                            name=item["name"],
                            type=item.get("type"),
                            description=item.get("description"),
                            data=samples.get(str(item["name"]), []),
                        )
                        for item in columns
                    ],
                    primary_keys=[item["name"] for item in columns if item.get("primaryKey")],
                )
            )
            for foreign_key in await connector.foreign_keys(table_name):
                target_table = str(foreign_key["targetTableName"])
                if target_table and target_table.casefold() not in selected_tables:
                    continue
                foreign_keys.append(
                    f"{foreign_key['sourceTableName']}.{foreign_key['sourceColumnName']} -> "
                    f"{foreign_key['targetTableName']}.{foreign_key['targetColumnName']}"
                )
        return SchemaDTO(
            name=database_and_schema(source)[0],
            table_count=len(table_models),
            table=table_models,
            foreign_keys=foreign_keys,
        )


def password_safe_view(source: Datasource) -> dict[str, Any]:
    from dataagent.serialization import serialize

    # Java marks both fields WRITE_ONLY. Keep a stable password placeholder for
    # the existing Nuxt edit form, but never reflect a potentially credentialed URL.
    payload = serialize(source, exclude={"password", "connection_url"})
    payload["password"] = "****" if source.password else ""
    return payload
