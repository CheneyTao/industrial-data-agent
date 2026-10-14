from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from dataagent.app import create_app
from dataagent.config import Settings
from dataagent.dashboard import DashboardService
from dataagent.database import Database


def _build_dashboard_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE pdm_machines (machineID INTEGER, model TEXT, age INTEGER);
        CREATE TABLE pdm_telemetry (
            datetime TEXT, machineID INTEGER, volt REAL, rotate REAL, pressure REAL, vibration REAL
        );
        CREATE TABLE pdm_errors (datetime TEXT, machineID INTEGER, errorID TEXT);
        CREATE TABLE pdm_maint (datetime TEXT, machineID INTEGER, comp TEXT);
        CREATE TABLE pdm_failures (datetime TEXT, machineID INTEGER, failure TEXT);
        CREATE TABLE dim_machine_models (
            source_model_code TEXT PRIMARY KEY, display_name_zh TEXT, display_name_en TEXT,
            sort_order INTEGER, is_demo_alias INTEGER, note TEXT
        );
        CREATE UNIQUE INDEX uq_machine ON pdm_machines(machineID);
        CREATE INDEX idx_telemetry_machine_datetime ON pdm_telemetry(machineID, datetime);
        CREATE INDEX idx_errors_machine_datetime ON pdm_errors(machineID, datetime);
        CREATE INDEX idx_maint_machine_datetime ON pdm_maint(machineID, datetime);
        CREATE INDEX idx_failures_machine_datetime ON pdm_failures(machineID, datetime);
        """
    )
    connection.executemany(
        "INSERT INTO pdm_machines(machineID, model, age) VALUES (?, ?, ?)",
        [(1, "model1", 5), (2, "model2", 10), (3, "model1", 15)],
    )
    connection.executemany(
        "INSERT INTO dim_machine_models VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("model1", "A 型机组", "Model A", 1, 1, "test"),
            ("model2", "B 型机组", "Model B", 2, 1, "test"),
        ],
    )
    telemetry = []
    for machine_id in (1, 2, 3):
        for day in range(1, 11):
            telemetry.append(
                (
                    f"2024-01-{day:02d} 00:00:00",
                    machine_id,
                    100 + machine_id + day,
                    200 + machine_id + day,
                    300 + machine_id + day,
                    10 + machine_id + day / 10,
                )
            )
    connection.executemany("INSERT INTO pdm_telemetry VALUES (?, ?, ?, ?, ?, ?)", telemetry)
    connection.executemany(
        "INSERT INTO pdm_errors VALUES (?, ?, ?)",
        [
            ("2024-01-02 03:00:00", 1, "error1"),
            ("2024-01-04 03:00:00", 1, "error1"),
            ("2024-01-06 03:00:00", 2, "error2"),
        ],
    )
    connection.executemany(
        "INSERT INTO pdm_maint VALUES (?, ?, ?)",
        [
            ("2024-01-04 00:00:00", 1, "comp1"),
            ("2024-01-06 00:00:00", 2, "comp2"),
        ],
    )
    connection.executemany(
        "INSERT INTO pdm_failures VALUES (?, ?, ?)",
        [
            ("2024-01-03 00:00:00", 1, "comp1"),
            ("2024-01-05 00:00:00", 1, "comp1"),
            ("2024-01-08 00:00:00", 2, "comp2"),
        ],
    )
    connection.commit()
    connection.close()


@pytest.mark.asyncio
async def test_report_watermark_reads_new_data_without_reusing_scope_cache(tmp_path: Path) -> None:
    source = tmp_path / "industrial.db"
    _build_dashboard_source(source)
    service = DashboardService(
        Settings(_env_file=None, dashboard_database_url=f"sqlite:///{source.as_posix()}")
    )
    try:
        assert await service._data_range() == (date(2024, 1, 1), date(2024, 1, 10))
        connection = sqlite3.connect(source)
        connection.execute(
            "INSERT INTO pdm_telemetry VALUES (?, ?, ?, ?, ?, ?)",
            ("2024-01-11 00:00:00", 1, 100, 200, 300, 10),
        )
        connection.commit()
        connection.close()
        assert await service.data_range() == (date(2024, 1, 1), date(2024, 1, 11))
        assert service._range == (date(2024, 1, 1), date(2024, 1, 10))
    finally:
        await service.close()


@pytest.fixture
async def dashboard_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    source = tmp_path / "industrial.db"
    _build_dashboard_source(source)
    settings = Settings(
        _env_file=None,
        management_database_url=f"sqlite+aiosqlite:///{(tmp_path / 'management.db').as_posix()}",
        dashboard_database_url=f"sqlite:///{source.as_posix()}",
        model_mode="mock",
        embedding_mode="database",
        vector_file_path=tmp_path / "vectors.json",
        vector_embedding_dimension=16,
        file_path=tmp_path / "uploads",
    )
    settings.ensure_directories()
    database = Database(settings)
    await database.create_schema()
    app = create_app(settings, database)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await app.state.dashboard.close()
    await app.state.telemetry.close()
    await database.close()


@pytest.mark.asyncio
async def test_dashboard_endpoints_use_real_aggregates(dashboard_client: httpx.AsyncClient) -> None:
    options = (await dashboard_client.get("/api/dashboard/filters")).json()
    assert options["dataRange"] == {"startDate": "2024-01-01", "endDate": "2024-01-10"}
    assert [item["value"] for item in options["models"]] == ["model1", "model2"]
    assert len(options["machines"]) == 3

    params = {"start_date": "2024-01-01", "end_date": "2024-01-10"}
    overview_response = await dashboard_client.get("/api/dashboard/overview", params=params)
    assert overview_response.status_code == 200
    overview = overview_response.json()
    assert overview["totalDevices"] == 3
    assert overview["failedDevices"] == 2
    assert overview["failureCount"] == 3
    assert overview["failureRatePct"] == pytest.approx(66.67)
    assert overview["errorCount"] == 3
    assert overview["maintenanceCount"] == 2
    assert overview["averageDeviceAge"] == pytest.approx(10.0)
    assert overview["mtbfHours"] == pytest.approx(10.0)
    assert overview["mttrHours"] is None

    trend = (
        await dashboard_client.get(
            "/api/dashboard/failure-trend", params={**params, "granularity": "day"}
        )
    ).json()
    assert [item["failureCount"] for item in trend["items"]] == [1, 1, 1]

    model_rows = (await dashboard_client.get("/api/dashboard/failure-by-model", params=params)).json()
    assert model_rows["items"][0]["model"] == "model1"
    assert model_rows["items"][0]["failureCount"] == 2

    failure_types = (await dashboard_client.get("/api/dashboard/failure-types", params=params)).json()
    assert {item["failureType"] for item in failure_types["items"]} == {"comp1", "comp2"}

    error_rows = (await dashboard_client.get("/api/dashboard/error-ranking", params=params)).json()
    assert error_rows["items"][0] == {
        "errorId": "error1",
        "errorCount": 2,
        "affectedMachineCount": 1,
    }

    machine_rows = (await dashboard_client.get("/api/dashboard/machine-ranking", params=params)).json()
    assert machine_rows["items"][0]["machineId"] == 1
    assert machine_rows["items"][0]["failureCount"] == 2

    telemetry = (
        await dashboard_client.get("/api/dashboard/machine/1/telemetry", params=params)
    ).json()
    assert telemetry["sourcePointCount"] == 10
    assert telemetry["returnedPointCount"] == 10
    assert telemetry["sampled"] is False
    assert set(telemetry["points"][0]) == {
        "datetime",
        "volt",
        "rotate",
        "pressure",
        "vibration",
        "samples",
    }

    maintenance = (
        await dashboard_client.get("/api/dashboard/maintenance-analysis", params=params)
    ).json()
    comp1 = next(item for item in maintenance["items"] if item["maintenanceType"] == "comp1")
    assert comp1["sameComponentFailuresBefore7d"] == 1
    assert comp1["sameComponentFailuresAfter7d"] == 1
    assert maintenance["summary"]["maintenanceCount"] == 2


@pytest.mark.asyncio
async def test_dashboard_filters_link_all_queries_and_context(
    dashboard_client: httpx.AsyncClient,
) -> None:
    params = {
        "start_date": "2024-01-01",
        "end_date": "2024-01-10",
        "model": "model1",
        "failure_type": "comp1",
    }
    overview = (await dashboard_client.get("/api/dashboard/overview", params=params)).json()
    assert overview["totalDevices"] == 2
    assert overview["failedDevices"] == 1
    assert overview["failureCount"] == 2
    # Error and maintenance events are scoped to machines that had comp1 failures.
    assert overview["errorCount"] == 2
    assert overview["maintenanceCount"] == 1

    ranking = (await dashboard_client.get("/api/dashboard/machine-ranking", params=params)).json()
    assert [item["machineId"] for item in ranking["items"]] == [1]

    context_response = await dashboard_client.post("/api/dashboard/analysis-context", params=params)
    assert context_response.status_code == 200
    context = context_response.json()
    assert "Planner、SQL Tool、Python/Pandas Tool、RAG、归因分析和报告生成链路" in context["question"]
    assert context["scope"]["model"] == "model1"
    assert context["scope"]["failureType"] == "comp1"
    assert context["evidence"]["overview"]["failureCount"] == 2


@pytest.mark.asyncio
async def test_dashboard_returns_actionable_errors(dashboard_client: httpx.AsyncClient) -> None:
    invalid_range = await dashboard_client.get(
        "/api/dashboard/overview",
        params={"start_date": "2024-02-01", "end_date": "2024-01-01"},
    )
    assert invalid_range.status_code == 400
    assert "start_date" in invalid_range.json()["message"]

    invalid_date = await dashboard_client.get(
        "/api/dashboard/overview", params={"start_date": "not-a-date"}
    )
    assert invalid_date.status_code == 400

    missing_machine = await dashboard_client.get(
        "/api/dashboard/machine/999/telemetry",
        params={"start_date": "2024-01-01", "end_date": "2024-01-10"},
    )
    assert missing_machine.status_code == 404
    assert "machineID=999" in missing_machine.json()["message"]

    invalid_granularity = await dashboard_client.get(
        "/api/dashboard/failure-trend", params={"granularity": "quarter"}
    )
    assert invalid_granularity.status_code == 400


@pytest.mark.asyncio
async def test_dashboard_index_audit_is_read_only(dashboard_client: httpx.AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/index-audit")
    assert response.status_code == 200
    payload = response.json()
    assert "idx_failures_machine_datetime" in {
        item["name"] for item in payload["existing"]["pdm_failures"]
    }
    assert payload["recommendations"][-1].startswith("仅在真实生产库")
