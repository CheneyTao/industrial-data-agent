from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd


READ_ONLY_PREFIXES = ("select", "with", "show", "describe", "desc", "explain")
FORBIDDEN_READ_ONLY = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|load\s+data)\b",
    re.IGNORECASE,
)


class Database:
    """Small DB-API adapter supporting SQLite verification and MySQL production use."""

    def __init__(self, url: str):
        self.url = url
        parsed = urlparse(url)
        self.dialect = parsed.scheme.split("+")[0].lower()
        if self.dialect not in {"sqlite", "mysql"}:
            raise ValueError("Only sqlite:/// and mysql:// URLs are supported")
        self._parsed = parsed

    @property
    def sqlite_path(self) -> Path:
        if self.dialect != "sqlite":
            raise ValueError("Not a SQLite URL")
        raw_path = unquote(self._parsed.path)
        if self._parsed.netloc:
            raw_path = f"//{self._parsed.netloc}{raw_path}"
        if raw_path in {"/:memory:", ":memory:"}:
            return Path(":memory:")
        # urlparse keeps a leading slash before a Windows drive and callers may
        # supply backslashes (sqlite:///C:\\path\\db.sqlite).  Path('/C:\\...')
        # is not an absolute Windows path and silently points at a different DB.
        if re.match(r"^/[A-Za-z]:[\\/]", raw_path):
            raw_path = raw_path[1:]
        return Path(raw_path).expanduser().resolve()

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.dialect == "sqlite":
            path = self.sqlite_path
            if path != Path(":memory:"):
                path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=60)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        else:
            try:
                import pymysql
            except ImportError as exc:
                raise RuntimeError("MySQL mode requires: pip install PyMySQL") from exc
            query = parse_qs(self._parsed.query)
            conn = pymysql.connect(
                host=self._parsed.hostname or "127.0.0.1",
                port=self._parsed.port or 3306,
                user=unquote(self._parsed.username or ""),
                password=unquote(self._parsed.password or ""),
                database=(self._parsed.path or "/industrial_pdm").lstrip("/"),
                charset=query.get("charset", ["utf8mb4"])[0],
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
            )
        try:
            yield conn
        finally:
            conn.close()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "mysql" else sql

    def _params(self, params: Sequence[Any]) -> tuple[Any, ...]:
        if self.dialect == "mysql":
            return tuple(params)
        return tuple(
            value.isoformat(sep=" ") if isinstance(value, datetime)
            else value.isoformat() if isinstance(value, date)
            else value
            for value in params
        )

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(self._sql(sql), self._params(params))
            rows = cursor.fetchall()
            if self.dialect == "sqlite":
                return [dict(row) for row in rows]
            return list(rows)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def query_df(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        return pd.DataFrame(self.fetch_all(sql, params))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(self._sql(sql), self._params(params))
            conn.commit()
            return int(cursor.rowcount)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.executemany(self._sql(sql), [self._params(row) for row in rows])
            conn.commit()
            return int(cursor.rowcount)

    def table_exists(self, table_name: str) -> bool:
        if self.dialect == "sqlite":
            row = self.fetch_one(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
                (table_name,),
            )
        else:
            row = self.fetch_one(
                "SELECT TABLE_NAME AS name FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?",
                (table_name,),
            )
        return row is not None

    def read_only_query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        normalized = sql.strip().rstrip(";").strip()
        if not normalized.lower().startswith(READ_ONLY_PREFIXES):
            raise ValueError("Only read-only SELECT/WITH/SHOW/DESCRIBE/EXPLAIN queries are allowed")
        if FORBIDDEN_READ_ONLY.search(normalized):
            raise ValueError("Potentially mutating SQL was rejected")
        if ";" in normalized:
            raise ValueError("Multiple SQL statements are not allowed")
        return self.fetch_all(normalized, params)
