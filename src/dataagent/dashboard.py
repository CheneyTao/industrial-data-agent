from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import SQLAlchemyError

from dataagent.bi.metric_registry import pdm_metric_registry, pdm_result_bindings
from dataagent.config import Settings
from dataagent.errors import ConfigurationError, DataAgentError, NotFoundError
from dataagent.query_execution import run_read_operation

logger = logging.getLogger(__name__)

T = TypeVar("T")

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "pdm_machines": {"machineID", "model", "age"},
    "pdm_telemetry": {"datetime", "machineID", "volt", "rotate", "pressure", "vibration"},
    "pdm_errors": {"datetime", "machineID", "errorID"},
    "pdm_maint": {"datetime", "machineID", "comp"},
    "pdm_failures": {"datetime", "machineID", "failure"},
}


@dataclass(frozen=True, slots=True)
class DashboardFilterInput:
    start_date: date | None = None
    end_date: date | None = None
    machine_id: int | None = None
    model: str | None = None
    failure_type: str | None = None


@dataclass(frozen=True, slots=True)
class DashboardScope:
    start_date: date
    end_date: date
    machine_id: int | None = None
    model: str | None = None
    failure_type: str | None = None

    @property
    def start_ts(self) -> str:
        return datetime.combine(self.start_date, time.min).isoformat(sep=" ")

    @property
    def end_ts(self) -> str:
        return datetime.combine(self.end_date + timedelta(days=1), time.min).isoformat(sep=" ")

    def parameters(self) -> dict[str, Any]:
        return {
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "machine_id": self.machine_id,
            "model": self.model,
            "failure_type": self.failure_type,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "startDate": self.start_date.isoformat(),
            "endDate": self.end_date.isoformat(),
            "machineId": self.machine_id,
            "model": self.model,
            "failureType": self.failure_type,
        }


class DashboardRepository:
    """Read-only aggregate queries over the verified industrial PdM source."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = make_url(settings.dashboard_database_url)
        self._engine: Engine | None = None
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._has_model_dimension = False

    def _sqlite_path(self) -> Path | None:
        if self.url.get_backend_name() != "sqlite" or self.url.database in {None, ":memory:"}:
            return None
        return Path(self.url.database).expanduser().resolve()

    def _create_engine(self) -> Engine:
        sqlite_path = self._sqlite_path()
        if sqlite_path is not None and not sqlite_path.is_file():
            raise ConfigurationError(f"Dashboard database does not exist: {sqlite_path}")
        return create_engine(
            self.url,
            pool_pre_ping=True,
            future=True,
            connect_args={"check_same_thread": False} if self.url.get_backend_name() == "sqlite" else {},
        )

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return

            def verify() -> tuple[Engine, bool]:
                engine = self._engine or self._create_engine()
                inspector = inspect(engine)
                tables = set(inspector.get_table_names()) | set(inspector.get_view_names())
                missing_tables = sorted(set(REQUIRED_COLUMNS) - tables)
                if missing_tables:
                    engine.dispose()
                    raise ConfigurationError(
                        "Dashboard source is missing required tables: " + ", ".join(missing_tables)
                    )
                problems: list[str] = []
                for table_name, required in REQUIRED_COLUMNS.items():
                    actual = {str(item["name"]) for item in inspector.get_columns(table_name)}
                    missing = sorted(required - actual)
                    if missing:
                        problems.append(f"{table_name}({', '.join(missing)})")
                if problems:
                    engine.dispose()
                    raise ConfigurationError(
                        "Dashboard source schema is incompatible: " + "; ".join(problems)
                    )
                return engine, "dim_machine_models" in tables

            try:
                engine, has_dimension = await asyncio.wait_for(
                    asyncio.to_thread(verify), self.settings.dashboard_query_timeout_seconds
                )
            except TimeoutError as exc:
                raise DataAgentError("Dashboard database validation timed out", status_code=504) from exc
            except DataAgentError:
                raise
            except (SQLAlchemyError, OSError) as exc:
                logger.exception("Dashboard database validation failed")
                raise ConfigurationError(f"Dashboard database is unavailable ({type(exc).__name__})") from exc
            self._engine = engine
            self._has_model_dimension = has_dimension
            self._ready = True

    async def _run(self, operation: Callable[[Connection], T]) -> T:
        await self.ensure_ready()
        assert self._engine is not None

        try:
            return await run_read_operation(
                self._engine, operation, self.settings.dashboard_query_timeout_seconds
            )
        except TimeoutError as exc:
            raise DataAgentError("Dashboard query timed out", status_code=504) from exc
        except DataAgentError:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Dashboard query failed")
            raise DataAgentError(f"Dashboard query failed ({type(exc).__name__})", status_code=503) from exc

    @staticmethod
    def _rows(connection: Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result = connection.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings().all()]

    @staticmethod
    def _row(connection: Connection, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = connection.execute(text(sql), params or {}).mappings().first()
        return dict(result) if result is not None else {}

    def _model_join(self) -> tuple[str, str]:
        if self._has_model_dimension:
            return (
                "LEFT JOIN dim_machine_models d ON d.source_model_code = m.model",
                "COALESCE(d.display_name_zh, m.model)",
            )
        return "", "m.model"

    @staticmethod
    def _scope_ctes() -> str:
        return """
            base_machines AS (
                SELECT m.machineID, m.model, m.age
                FROM pdm_machines m
                WHERE (:machine_id IS NULL OR m.machineID = :machine_id)
                  AND (:model IS NULL OR m.model = :model)
            ),
            event_machines AS (
                SELECT b.machineID, b.model, b.age
                FROM base_machines b
                WHERE :failure_type IS NULL OR EXISTS (
                    SELECT 1
                    FROM pdm_failures sf
                    WHERE sf.machineID = b.machineID
                      AND sf.datetime >= :start_ts
                      AND sf.datetime < :end_ts
                      AND sf.failure = :failure_type
                )
            )
        """

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return None if not math.isfinite(value) else value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    @classmethod
    def _clean_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        return {key: cls._json_value(value) for key, value in row.items()}

    async def data_range(self) -> tuple[date, date]:
        def operation(connection: Connection) -> tuple[date, date]:
            row = self._row(
                connection,
                """
                SELECT MIN(event_date) AS start_date, MAX(event_date) AS end_date
                FROM (
                    SELECT MIN(datetime) AS event_date FROM pdm_telemetry
                    UNION ALL SELECT MIN(datetime) FROM pdm_errors
                    UNION ALL SELECT MIN(datetime) FROM pdm_maint
                    UNION ALL SELECT MIN(datetime) FROM pdm_failures
                    UNION ALL SELECT MAX(datetime) FROM pdm_telemetry
                    UNION ALL SELECT MAX(datetime) FROM pdm_errors
                    UNION ALL SELECT MAX(datetime) FROM pdm_maint
                    UNION ALL SELECT MAX(datetime) FROM pdm_failures
                ) ranges
                WHERE event_date IS NOT NULL
                """,
            )
            if not row.get("start_date") or not row.get("end_date"):
                raise ConfigurationError("Dashboard source contains no dated industrial records")
            return (
                date.fromisoformat(str(row["start_date"])[:10]),
                date.fromisoformat(str(row["end_date"])[:10]),
            )

        return await self._run(operation)

    async def machine_exists(self, machine_id: int, model: str | None) -> bool:
        def operation(connection: Connection) -> bool:
            row = self._row(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM pdm_machines
                WHERE machineID = :machine_id
                  AND (:model IS NULL OR model = :model)
                """,
                {"machine_id": machine_id, "model": model},
            )
            return bool(row.get("count"))

        return await self._run(operation)

    async def filter_options(self) -> dict[str, Any]:
        start_date, end_date = await self.data_range()

        def operation(connection: Connection) -> dict[str, Any]:
            model_join, model_label = self._model_join()
            models = self._rows(
                connection,
                f"""
                SELECT m.model AS value, {model_label} AS label, COUNT(*) AS machine_count
                FROM pdm_machines m
                {model_join}
                GROUP BY m.model, {model_label}
                ORDER BY m.model
                """,
            )
            machines = self._rows(
                connection,
                f"""
                SELECT m.machineID AS id, m.model, {model_label} AS model_label, m.age
                FROM pdm_machines m
                {model_join}
                ORDER BY m.machineID
                """,
            )
            failures = self._rows(
                connection,
                "SELECT failure AS value, COUNT(*) AS event_count "
                "FROM pdm_failures GROUP BY failure ORDER BY failure",
            )
            return {
                "models": [
                    {
                        "value": row.get("value"),
                        "label": row.get("label"),
                        "machineCount": int(row.get("machine_count") or 0),
                    }
                    for row in models
                ],
                "machines": [
                    {
                        "id": int(row["id"]),
                        "model": row.get("model"),
                        "modelLabel": row.get("model_label"),
                        "age": int(row["age"]) if row.get("age") is not None else None,
                    }
                    for row in machines
                ],
                "failureTypes": [
                    {
                        "value": row.get("value"),
                        "eventCount": int(row.get("event_count") or 0),
                    }
                    for row in failures
                ],
            }

        options = await self._run(operation)
        default_start = max(start_date, end_date - timedelta(days=29))
        return {
            "dataRange": {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
            "defaultRange": {
                "startDate": default_start.isoformat(),
                "endDate": end_date.isoformat(),
            },
            "presets": [
                {"label": "最近7天", "days": 7},
                {"label": "最近30天", "days": 30},
                {"label": "最近90天", "days": 90},
            ],
            "telemetryMetrics": ["volt", "rotate", "pressure", "vibration"],
            **options,
        }

    async def overview(self, scope: DashboardScope) -> dict[str, Any]:
        params = scope.parameters()
        params["recent_7_start"] = datetime.combine(
            max(scope.start_date, scope.end_date - timedelta(days=6)), time.min
        ).isoformat(sep=" ")
        params["recent_30_start"] = datetime.combine(
            max(scope.start_date, scope.end_date - timedelta(days=29)), time.min
        ).isoformat(sep=" ")

        def operation(connection: Connection) -> dict[str, Any]:
            row = self._row(
                connection,
                f"""
                WITH {self._scope_ctes()},
                failure_stats AS (
                    SELECT COUNT(*) AS failure_count,
                           COUNT(DISTINCT f.machineID) AS failed_machine_count
                    FROM pdm_failures f
                    JOIN base_machines b ON b.machineID = f.machineID
                    WHERE f.datetime >= :start_ts AND f.datetime < :end_ts
                      AND (:failure_type IS NULL OR f.failure = :failure_type)
                ),
                error_stats AS (
                    SELECT COUNT(*) AS error_count
                    FROM pdm_errors e
                    JOIN event_machines em ON em.machineID = e.machineID
                    WHERE e.datetime >= :start_ts AND e.datetime < :end_ts
                ),
                maintenance_stats AS (
                    SELECT COUNT(*) AS maintenance_count
                    FROM pdm_maint mt
                    JOIN event_machines em ON em.machineID = mt.machineID
                    WHERE mt.datetime >= :start_ts AND mt.datetime < :end_ts
                ),
                telemetry_stats AS (
                    SELECT COUNT(*) AS telemetry_points
                    FROM pdm_telemetry t
                    JOIN base_machines b ON b.machineID = t.machineID
                    WHERE t.datetime >= :start_ts AND t.datetime < :end_ts
                ),
                recent_7 AS (
                    SELECT COUNT(*) AS failure_count
                    FROM pdm_failures f
                    JOIN base_machines b ON b.machineID = f.machineID
                    WHERE f.datetime >= :recent_7_start AND f.datetime < :end_ts
                      AND (:failure_type IS NULL OR f.failure = :failure_type)
                ),
                recent_30 AS (
                    SELECT COUNT(DISTINCT f.machineID) AS failed_machine_count
                    FROM pdm_failures f
                    JOIN base_machines b ON b.machineID = f.machineID
                    WHERE f.datetime >= :recent_30_start AND f.datetime < :end_ts
                      AND (:failure_type IS NULL OR f.failure = :failure_type)
                )
                SELECT
                    (SELECT COUNT(*) FROM base_machines) AS total_devices,
                    (SELECT AVG(age) FROM base_machines) AS average_device_age,
                    fs.failed_machine_count,
                    fs.failure_count,
                    es.error_count,
                    ms.maintenance_count,
                    ts.telemetry_points,
                    r7.failure_count AS recent_7_failure_count,
                    r30.failed_machine_count AS recent_30_failed_machine_count
                FROM failure_stats fs, error_stats es, maintenance_stats ms,
                     telemetry_stats ts, recent_7 r7, recent_30 r30
                """,
                params,
            )
            total_devices = int(row.get("total_devices") or 0)
            failed_devices = int(row.get("failed_machine_count") or 0)
            failures = int(row.get("failure_count") or 0)
            telemetry_points = int(row.get("telemetry_points") or 0)
            recent_30_failed = int(row.get("recent_30_failed_machine_count") or 0)
            return {
                "totalDevices": total_devices,
                "failedDevices": failed_devices,
                "failureCount": failures,
                "failureRatePct": pdm_result_bindings(["failure_rate_pct"], None)[0].read(
                    {"failedDevices": failed_devices, "totalDevices": total_devices}
                ),
                "errorCount": int(row.get("error_count") or 0),
                "maintenanceCount": int(row.get("maintenance_count") or 0),
                "averageDeviceAge": round(float(row["average_device_age"]), 2)
                if row.get("average_device_age") is not None
                else None,
                "mtbfHours": round(telemetry_points / failures, 2) if failures else None,
                "mttrHours": None,
                "mttrAvailability": "源数据没有修复完成时间或维修时长，无法可靠计算 MTTR",
                "recent7FailureCount": int(row.get("recent_7_failure_count") or 0),
                "recent30FailureRatePct": pdm_result_bindings(["failure_rate_pct"], None)[0].read(
                    {"failedDevices": recent_30_failed, "totalDevices": total_devices}
                ),
                "telemetryPoints": telemetry_points,
                "definitions": {
                    "failureRatePct": "筛选期内发生至少一次故障的设备数 / 筛选设备总数",
                    "mtbfHours": "筛选期内遥测观测设备小时数 / 故障事件数",
                    "recent30FailureRatePct": "结束日前最多30天内故障设备数 / 筛选设备总数",
                },
                "scope": scope.as_dict(),
            }

        return await self._run(operation)

    def _period_expression(self, granularity: str) -> str:
        backend = self.url.get_backend_name()
        if backend == "mysql":
            return {
                "day": "DATE(f.datetime)",
                "week": "DATE_SUB(DATE(f.datetime), INTERVAL WEEKDAY(f.datetime) DAY)",
                "month": "DATE_FORMAT(f.datetime, '%Y-%m-01')",
            }[granularity]
        return {
            "day": "substr(f.datetime, 1, 10)",
            "week": (
                "date(f.datetime, '-' || ((CAST(strftime('%w', f.datetime) AS INTEGER) + 6) % 7) || ' days')"
            ),
            "month": "substr(f.datetime, 1, 7) || '-01'",
        }[granularity]

    async def failure_trend(self, scope: DashboardScope, granularity: str) -> list[dict[str, Any]]:
        period = self._period_expression(granularity)

        def operation(connection: Connection) -> list[dict[str, Any]]:
            rows = self._rows(
                connection,
                f"""
                WITH {self._scope_ctes()}
                SELECT {period} AS period, COUNT(*) AS failure_count,
                       COUNT(DISTINCT f.machineID) AS failed_machine_count
                FROM pdm_failures f
                JOIN base_machines b ON b.machineID = f.machineID
                WHERE f.datetime >= :start_ts AND f.datetime < :end_ts
                  AND (:failure_type IS NULL OR f.failure = :failure_type)
                GROUP BY {period}
                ORDER BY {period}
                """,
                scope.parameters(),
            )
            return [
                {
                    "period": self._json_value(row.get("period")),
                    "failureCount": int(row.get("failure_count") or 0),
                    "failedMachineCount": int(row.get("failed_machine_count") or 0),
                }
                for row in rows
            ]

        return await self._run(operation)

    async def failure_by_model(self, scope: DashboardScope) -> list[dict[str, Any]]:
        def operation(connection: Connection) -> list[dict[str, Any]]:
            model_join, model_label = self._model_join()
            rows = self._rows(
                connection,
                f"""
                WITH {self._scope_ctes()},
                failures AS (
                    SELECT f.machineID, COUNT(*) AS failure_count
                    FROM pdm_failures f
                    JOIN base_machines b ON b.machineID = f.machineID
                    WHERE f.datetime >= :start_ts AND f.datetime < :end_ts
                      AND (:failure_type IS NULL OR f.failure = :failure_type)
                    GROUP BY f.machineID
                )
                SELECT m.model, {model_label} AS model_label,
                       COUNT(*) AS device_count,
                       SUM(CASE WHEN COALESCE(f.failure_count, 0) > 0 THEN 1 ELSE 0 END)
                           AS failed_device_count,
                       COALESCE(SUM(f.failure_count), 0) AS failure_count
                FROM base_machines b
                JOIN pdm_machines m ON m.machineID = b.machineID
                {model_join}
                LEFT JOIN failures f ON f.machineID = m.machineID
                GROUP BY m.model, {model_label}
                ORDER BY failed_device_count DESC, failure_count DESC, m.model
                """,
                scope.parameters(),
            )
            result = []
            for row in rows:
                devices = int(row.get("device_count") or 0)
                failed = int(row.get("failed_device_count") or 0)
                failures = int(row.get("failure_count") or 0)
                result.append(
                    {
                        "model": row.get("model"),
                        "modelLabel": row.get("model_label"),
                        "deviceCount": devices,
                        "failedDeviceCount": failed,
                        "failureCount": failures,
                        "failureRatePct": pdm_result_bindings(["failure_rate_pct"], "model")[0].read(
                            {"failedDeviceCount": failed, "deviceCount": devices}
                        ),
                        "failureEventsPerDevice": round(failures / devices, 3) if devices else None,
                    }
                )
            return result

        return await self._run(operation)

    async def failure_types(self, scope: DashboardScope) -> list[dict[str, Any]]:
        def operation(connection: Connection) -> list[dict[str, Any]]:
            rows = self._rows(
                connection,
                f"""
                WITH {self._scope_ctes()}
                SELECT f.failure AS failure_type, COUNT(*) AS failure_count,
                       COUNT(DISTINCT f.machineID) AS failed_machine_count
                FROM pdm_failures f
                JOIN base_machines b ON b.machineID = f.machineID
                WHERE f.datetime >= :start_ts AND f.datetime < :end_ts
                  AND (:failure_type IS NULL OR f.failure = :failure_type)
                GROUP BY f.failure
                ORDER BY failure_count DESC, f.failure
                """,
                scope.parameters(),
            )
            total = sum(int(row.get("failure_count") or 0) for row in rows)
            return [
                {
                    "failureType": row.get("failure_type"),
                    "failureCount": int(row.get("failure_count") or 0),
                    "failedMachineCount": int(row.get("failed_machine_count") or 0),
                    "sharePct": round(int(row.get("failure_count") or 0) / total * 100, 2) if total else 0.0,
                }
                for row in rows
            ]

        return await self._run(operation)

    async def error_ranking(self, scope: DashboardScope, limit: int) -> list[dict[str, Any]]:
        params = {**scope.parameters(), "limit": limit}

        def operation(connection: Connection) -> list[dict[str, Any]]:
            rows = self._rows(
                connection,
                f"""
                WITH {self._scope_ctes()}
                SELECT e.errorID AS error_id, COUNT(*) AS error_count,
                       COUNT(DISTINCT e.machineID) AS affected_machine_count
                FROM pdm_errors e
                JOIN event_machines em ON em.machineID = e.machineID
                WHERE e.datetime >= :start_ts AND e.datetime < :end_ts
                GROUP BY e.errorID
                ORDER BY error_count DESC, e.errorID
                LIMIT :limit
                """,
                params,
            )
            return [
                {
                    "errorId": row.get("error_id"),
                    "errorCount": int(row.get("error_count") or 0),
                    "affectedMachineCount": int(row.get("affected_machine_count") or 0),
                }
                for row in rows
            ]

        return await self._run(operation)

    async def machine_ranking(self, scope: DashboardScope, limit: int) -> list[dict[str, Any]]:
        params = {**scope.parameters(), "limit": limit}

        def operation(connection: Connection) -> list[dict[str, Any]]:
            model_join, model_label = self._model_join()
            rows = self._rows(
                connection,
                f"""
                WITH {self._scope_ctes()},
                failure_counts AS (
                    SELECT f.machineID, COUNT(*) AS failure_count
                    FROM pdm_failures f
                    JOIN base_machines b ON b.machineID = f.machineID
                    WHERE f.datetime >= :start_ts AND f.datetime < :end_ts
                      AND (:failure_type IS NULL OR f.failure = :failure_type)
                    GROUP BY f.machineID
                ),
                error_counts AS (
                    SELECT e.machineID, COUNT(*) AS error_count
                    FROM pdm_errors e
                    JOIN event_machines em ON em.machineID = e.machineID
                    WHERE e.datetime >= :start_ts AND e.datetime < :end_ts
                    GROUP BY e.machineID
                ),
                maintenance_counts AS (
                    SELECT mt.machineID, COUNT(*) AS maintenance_count
                    FROM pdm_maint mt
                    JOIN event_machines em ON em.machineID = mt.machineID
                    WHERE mt.datetime >= :start_ts AND mt.datetime < :end_ts
                    GROUP BY mt.machineID
                )
                SELECT m.machineID, m.model, {model_label} AS model_label, m.age,
                       COALESCE(fc.failure_count, 0) AS failure_count,
                       COALESCE(ec.error_count, 0) AS error_count,
                       COALESCE(mc.maintenance_count, 0) AS maintenance_count
                FROM event_machines em
                JOIN pdm_machines m ON m.machineID = em.machineID
                {model_join}
                LEFT JOIN failure_counts fc ON fc.machineID = m.machineID
                LEFT JOIN error_counts ec ON ec.machineID = m.machineID
                LEFT JOIN maintenance_counts mc ON mc.machineID = m.machineID
                ORDER BY failure_count DESC, error_count DESC, maintenance_count DESC, m.machineID
                LIMIT :limit
                """,
                params,
            )
            return [
                {
                    "machineId": int(row["machineID"]),
                    "model": row.get("model"),
                    "modelLabel": row.get("model_label"),
                    "age": int(row["age"]) if row.get("age") is not None else None,
                    "failureCount": int(row.get("failure_count") or 0),
                    "errorCount": int(row.get("error_count") or 0),
                    "maintenanceCount": int(row.get("maintenance_count") or 0),
                }
                for row in rows
            ]

        return await self._run(operation)

    async def telemetry(self, scope: DashboardScope, machine_id: int, max_points: int) -> dict[str, Any]:
        params = {**scope.parameters(), "selected_machine_id": machine_id}
        bucket_expression = (
            "FLOOR((row_number - 1) / :bucket_size)"
            if self.url.get_backend_name() == "mysql"
            else "CAST((row_number - 1) / :bucket_size AS INTEGER)"
        )

        def operation(connection: Connection) -> dict[str, Any]:
            count_row = self._row(
                connection,
                """
                SELECT COUNT(*) AS point_count
                FROM pdm_telemetry t
                JOIN pdm_machines m ON m.machineID = t.machineID
                WHERE t.machineID = :selected_machine_id
                  AND t.datetime >= :start_ts AND t.datetime < :end_ts
                  AND (:model IS NULL OR m.model = :model)
                """,
                params,
            )
            point_count = int(count_row.get("point_count") or 0)
            if not point_count:
                return {
                    "machineId": machine_id,
                    "sourcePointCount": 0,
                    "returnedPointCount": 0,
                    "sampled": False,
                    "points": [],
                }
            bucket_size = max(1, math.ceil(point_count / max_points))
            query_params = {**params, "bucket_size": bucket_size}
            rows = self._rows(
                connection,
                f"""
                WITH filtered AS (
                    SELECT t.datetime, t.volt, t.rotate, t.pressure, t.vibration,
                           ROW_NUMBER() OVER (ORDER BY t.datetime) AS row_number
                    FROM pdm_telemetry t
                    JOIN pdm_machines m ON m.machineID = t.machineID
                    WHERE t.machineID = :selected_machine_id
                      AND t.datetime >= :start_ts AND t.datetime < :end_ts
                      AND (:model IS NULL OR m.model = :model)
                )
                SELECT MIN(datetime) AS datetime,
                       AVG(volt) AS volt,
                       AVG(rotate) AS rotate,
                       AVG(pressure) AS pressure,
                       AVG(vibration) AS vibration,
                       COUNT(*) AS samples
                FROM filtered
                GROUP BY {bucket_expression}
                ORDER BY MIN(datetime)
                """,
                query_params,
            )
            points = []
            for row in rows:
                points.append(
                    {
                        "datetime": self._json_value(row.get("datetime")),
                        "volt": round(float(row["volt"]), 4) if row.get("volt") is not None else None,
                        "rotate": round(float(row["rotate"]), 4) if row.get("rotate") is not None else None,
                        "pressure": round(float(row["pressure"]), 4)
                        if row.get("pressure") is not None
                        else None,
                        "vibration": round(float(row["vibration"]), 4)
                        if row.get("vibration") is not None
                        else None,
                        "samples": int(row.get("samples") or 0),
                    }
                )
            return {
                "machineId": machine_id,
                "sourcePointCount": point_count,
                "returnedPointCount": len(points),
                "sampled": bucket_size > 1,
                "bucketSize": bucket_size,
                "points": points,
            }

        return await self._run(operation)

    def _maintenance_date_expressions(self) -> tuple[str, str]:
        if self.url.get_backend_name() == "mysql":
            return (
                "DATE_SUB(mt.datetime, INTERVAL 7 DAY)",
                "DATE_ADD(mt.datetime, INTERVAL 7 DAY)",
            )
        return "datetime(mt.datetime, '-7 days')", "datetime(mt.datetime, '+7 days')"

    async def maintenance_analysis(self, scope: DashboardScope) -> dict[str, Any]:
        before_start, after_end = self._maintenance_date_expressions()

        def operation(connection: Connection) -> dict[str, Any]:
            rows = self._rows(
                connection,
                f"""
                WITH {self._scope_ctes()},
                maintenance_events AS (
                    SELECT mt.datetime, mt.machineID, mt.comp
                    FROM pdm_maint mt
                    JOIN event_machines em ON em.machineID = mt.machineID
                    WHERE mt.datetime >= :start_ts AND mt.datetime < :end_ts
                )
                SELECT mt.comp AS maintenance_type,
                       COUNT(*) AS maintenance_count,
                       SUM((
                           SELECT COUNT(*) FROM pdm_failures f
                           WHERE f.machineID = mt.machineID
                             AND f.failure = mt.comp
                             AND f.datetime >= {before_start}
                             AND f.datetime < mt.datetime
                             AND f.datetime >= :start_ts
                             AND (:failure_type IS NULL OR f.failure = :failure_type)
                       )) AS same_component_failures_before_7d,
                       SUM((
                           SELECT COUNT(*) FROM pdm_failures f
                           WHERE f.machineID = mt.machineID
                             AND f.failure = mt.comp
                             AND f.datetime > mt.datetime
                             AND f.datetime <= {after_end}
                             AND f.datetime < :end_ts
                             AND (:failure_type IS NULL OR f.failure = :failure_type)
                       )) AS same_component_failures_after_7d,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1 FROM pdm_failures f
                           WHERE f.machineID = mt.machineID
                             AND f.failure = mt.comp
                             AND f.datetime > mt.datetime
                             AND f.datetime <= {after_end}
                             AND f.datetime < :end_ts
                             AND (:failure_type IS NULL OR f.failure = :failure_type)
                       ) THEN 1 ELSE 0 END) AS maintenance_with_followup_failure
                FROM maintenance_events mt
                GROUP BY mt.comp
                ORDER BY maintenance_count DESC, mt.comp
                """,
                scope.parameters(),
            )
            items: list[dict[str, Any]] = []
            for row in rows:
                maintenance_count = int(row.get("maintenance_count") or 0)
                related = int(row.get("maintenance_with_followup_failure") or 0)
                items.append(
                    {
                        "maintenanceType": row.get("maintenance_type"),
                        "maintenanceCount": maintenance_count,
                        "sameComponentFailuresBefore7d": int(
                            row.get("same_component_failures_before_7d") or 0
                        ),
                        "sameComponentFailuresAfter7d": int(row.get("same_component_failures_after_7d") or 0),
                        "maintenanceWithFollowupFailure": related,
                        "followupFailureRatePct": round(related / maintenance_count * 100, 2)
                        if maintenance_count
                        else 0.0,
                    }
                )
            total_maintenance = sum(item["maintenanceCount"] for item in items)
            total_related = sum(item["maintenanceWithFollowupFailure"] for item in items)
            return {
                "windowDays": 7,
                "items": items,
                "summary": {
                    "maintenanceCount": total_maintenance,
                    "sameComponentFailuresBefore7d": sum(
                        item["sameComponentFailuresBefore7d"] for item in items
                    ),
                    "sameComponentFailuresAfter7d": sum(
                        item["sameComponentFailuresAfter7d"] for item in items
                    ),
                    "maintenanceWithFollowupFailure": total_related,
                    "followupFailureRatePct": round(total_related / total_maintenance * 100, 2)
                    if total_maintenance
                    else 0.0,
                },
                "definition": (
                    "同部件维修事件前后7天内、同一设备同一部件的来源故障事件；"
                    "时间窗口在全局筛选范围内截断，表示时序关联而非因果结论"
                ),
            }

        return await self._run(operation)

    async def index_audit(self) -> dict[str, Any]:
        def operation(connection: Connection) -> dict[str, Any]:
            inspector = inspect(connection)
            indexes: dict[str, list[dict[str, Any]]] = {}
            for table_name in REQUIRED_COLUMNS:
                table_indexes = []
                for item in inspector.get_indexes(table_name):
                    table_indexes.append(
                        {
                            "name": item.get("name"),
                            "columns": list(item.get("column_names") or []),
                            "unique": bool(item.get("unique")),
                        }
                    )
                indexes[table_name] = table_indexes
            return {
                "existing": indexes,
                "recommendations": [
                    "pdm_failures(failure, datetime, machineID) 可加速故障类型与时间组合筛选",
                    "pdm_machines(model, machineID) 可加速型号联动筛选",
                    "仅在真实生产库通过执行计划确认收益后创建；Dashboard 不自动修改索引",
                ],
            }

        return await self._run(operation)

    async def close(self) -> None:
        engine, self._engine = self._engine, None
        self._ready = False
        if engine is not None:
            await asyncio.to_thread(engine.dispose)


class DashboardService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = DashboardRepository(settings)
        self._range: tuple[date, date] | None = None
        self._range_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.repository.close()

    async def data_range(self) -> tuple[date, date]:
        """Read current event-date bounds for report publication preflight.

        Do not reuse the interactive scope cache: a scheduler must observe
        newly arrived data rather than a watermark from an earlier request.
        """
        return await self.repository.data_range()

    async def _data_range(self) -> tuple[date, date]:
        if self._range is not None:
            return self._range
        async with self._range_lock:
            if self._range is None:
                self._range = await self.repository.data_range()
        return self._range

    async def scope(self, filters: DashboardFilterInput) -> DashboardScope:
        available_start, available_end = await self._data_range()
        start_date = filters.start_date or available_start
        end_date = filters.end_date or available_end
        if start_date > end_date:
            raise DataAgentError("start_date must be earlier than or equal to end_date")
        if filters.machine_id is not None and filters.machine_id <= 0:
            raise DataAgentError("machine_id must be a positive integer")
        model = filters.model.strip() if filters.model and filters.model.strip() else None
        failure_type = (
            filters.failure_type.strip() if filters.failure_type and filters.failure_type.strip() else None
        )
        if filters.machine_id is not None and not await self.repository.machine_exists(
            filters.machine_id, model
        ):
            qualifier = f" in model {model}" if model else ""
            raise NotFoundError(f"machineID={filters.machine_id} does not exist{qualifier}")
        return DashboardScope(
            start_date=start_date,
            end_date=end_date,
            machine_id=filters.machine_id,
            model=model,
            failure_type=failure_type,
        )

    async def filter_options(self) -> dict[str, Any]:
        return await self.repository.filter_options()

    async def overview(self, filters: DashboardFilterInput) -> dict[str, Any]:
        result = await self.repository.overview(await self.scope(filters))
        registry = pdm_metric_registry()
        result["metric_definitions"] = registry.public_definitions()
        result["metric_registry"] = registry.provenance()
        return result

    async def failure_trend(self, filters: DashboardFilterInput, granularity: str) -> dict[str, Any]:
        if granularity not in {"day", "week", "month"}:
            raise DataAgentError("granularity must be one of: day, week, month")
        scope = await self.scope(filters)
        rows = await self.repository.failure_trend(scope, granularity)
        registry = pdm_metric_registry()
        ids = ["failure_count", "failed_device_count"]
        return {
            "granularity": granularity,
            "items": rows,
            "scope": scope.as_dict(),
            "metric_definitions": registry.public_definitions(ids),
            "metric_registry": registry.provenance(ids),
        }

    async def failure_by_model(self, filters: DashboardFilterInput) -> dict[str, Any]:
        scope = await self.scope(filters)
        registry = pdm_metric_registry()
        ids = ["device_count", "failed_device_count", "failure_count", "failure_rate_pct"]
        return {
            "items": await self.repository.failure_by_model(scope),
            "scope": scope.as_dict(),
            "metric_definitions": registry.public_definitions(ids),
            "metric_registry": registry.provenance(ids),
        }

    async def failure_types(self, filters: DashboardFilterInput) -> dict[str, Any]:
        scope = await self.scope(filters)
        return {"items": await self.repository.failure_types(scope), "scope": scope.as_dict()}

    async def error_ranking(self, filters: DashboardFilterInput, limit: int = 10) -> dict[str, Any]:
        scope = await self.scope(filters)
        return {
            "items": await self.repository.error_ranking(scope, limit),
            "limit": limit,
            "scope": scope.as_dict(),
        }

    async def machine_ranking(self, filters: DashboardFilterInput, limit: int = 10) -> dict[str, Any]:
        scope = await self.scope(filters)
        return {
            "items": await self.repository.machine_ranking(scope, limit),
            "limit": limit,
            "scope": scope.as_dict(),
        }

    async def telemetry(
        self, filters: DashboardFilterInput, machine_id: int, max_points: int | None = None
    ) -> dict[str, Any]:
        merged = DashboardFilterInput(
            start_date=filters.start_date,
            end_date=filters.end_date,
            machine_id=machine_id,
            model=filters.model,
            failure_type=filters.failure_type,
        )
        scope = await self.scope(merged)
        limit = max_points or self.settings.dashboard_telemetry_max_points
        result = await self.repository.telemetry(scope, machine_id, limit)
        result["scope"] = scope.as_dict()
        return result

    async def maintenance_analysis(self, filters: DashboardFilterInput) -> dict[str, Any]:
        scope = await self.scope(filters)
        result = await self.repository.maintenance_analysis(scope)
        result["scope"] = scope.as_dict()
        return result

    async def analysis_context(self, filters: DashboardFilterInput) -> dict[str, Any]:
        scope = await self.scope(filters)
        overview, trend, failure_types, errors, machines, maintenance = await asyncio.gather(
            self.repository.overview(scope),
            self.repository.failure_trend(scope, "day"),
            self.repository.failure_types(scope),
            self.repository.error_ranking(scope, 10),
            self.repository.machine_ranking(scope, 10),
            self.repository.maintenance_analysis(scope),
        )
        evidence = {
            "scope": scope.as_dict(),
            "overview": overview,
            "failureTrend": trend,
            "failureTypes": failure_types,
            "errorRanking": errors,
            "machineRanking": machines,
            "maintenanceAnalysis": maintenance,
        }
        compact_evidence = {
            "scope": scope.as_dict(),
            "overview": {
                key: value for key, value in overview.items() if key not in {"definitions", "scope"}
            },
            "failureTrendRecent": trend[-12:],
            "failureTypes": failure_types[:5],
            "errorRanking": errors[:5],
            "machineRanking": machines[:5],
            "maintenance": {
                "summary": maintenance.get("summary", {}),
                "items": maintenance.get("items", [])[:4],
                "definition": maintenance.get("definition"),
            },
        }
        filter_text = ", ".join(
            [
                f"时间 {scope.start_date.isoformat()} 至 {scope.end_date.isoformat()}",
                f"型号 {scope.model}" if scope.model else "全部型号",
                f"设备 {scope.machine_id}" if scope.machine_id else "全部设备",
                f"故障类型 {scope.failure_type}" if scope.failure_type else "全部故障类型",
            ]
        )
        question = (
            f"请对工业设备在以下 Dashboard 筛选范围内做深度分析：{filter_text}。"
            "请复用当前 DataAgent 的 Planner、SQL Tool、Python/Pandas Tool、RAG、归因分析和报告生成链路，"
            "已验证的来源表为 pdm_machines、pdm_telemetry、pdm_errors、pdm_maint、pdm_failures，"
            "请以这些真实表名作为 Schema RAG 和 SQL 生成的检索锚点。"
            "先通过已激活工业数据源复核关键数字，再给出：1.数据摘要；2.主要异常；3.故障趋势；"
            "4.高风险设备；5.Error 与 Failure 关系；6.维修与故障关系；7.可能原因；8.数据证据。"
            "必须区分相关性与因果性，引用实际 SQL/统计结果；若数据源或工具不可用，明确说明缺口，"
            "不要编造。以下是 Dashboard 同一筛选口径下的确定性聚合快照，可作为待复核证据：\n"
            + json.dumps(compact_evidence, ensure_ascii=False, separators=(",", ":"))
        )
        return {"question": question, "evidence": evidence, "scope": scope.as_dict()}

    async def index_audit(self) -> dict[str, Any]:
        return await self.repository.index_audit()
