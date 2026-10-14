from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request

from dataagent.dashboard import DashboardFilterInput, DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _filters(
    start_date: date | None,
    end_date: date | None,
    machine_id: int | None,
    model: str | None,
    failure_type: str | None,
) -> DashboardFilterInput:
    return DashboardFilterInput(
        start_date=start_date,
        end_date=end_date,
        machine_id=machine_id,
        model=model,
        failure_type=failure_type,
    )


def _service(request: Request) -> DashboardService:
    return request.app.state.dashboard


@router.get("/filters")
async def dashboard_filters(request: Request):
    return await _service(request).filter_options()


@router.get("/overview")
async def overview(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
):
    return await _service(request).overview(
        _filters(start_date, end_date, machine_id, model, failure_type)
    )


@router.get("/failure-trend")
async def failure_trend(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
    granularity: str = Query("day", pattern="^(day|week|month)$"),
):
    return await _service(request).failure_trend(
        _filters(start_date, end_date, machine_id, model, failure_type), granularity
    )


@router.get("/failure-by-model")
async def failure_by_model(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
):
    return await _service(request).failure_by_model(
        _filters(start_date, end_date, machine_id, model, failure_type)
    )


@router.get("/failure-types")
async def failure_types(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
):
    return await _service(request).failure_types(
        _filters(start_date, end_date, machine_id, model, failure_type)
    )


@router.get("/error-ranking")
async def error_ranking(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
    limit: int = Query(10, ge=1, le=100),
):
    return await _service(request).error_ranking(
        _filters(start_date, end_date, machine_id, model, failure_type), limit
    )


@router.get("/machine-ranking")
async def machine_ranking(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
    limit: int = Query(10, ge=1, le=100),
):
    return await _service(request).machine_ranking(
        _filters(start_date, end_date, machine_id, model, failure_type), limit
    )


@router.get("/machine/{machine_id}/telemetry")
async def machine_telemetry(
    machine_id: int,
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    model: str | None = None,
    failure_type: str | None = None,
    max_points: int | None = Query(None, ge=50, le=2000),
):
    return await _service(request).telemetry(
        _filters(start_date, end_date, machine_id, model, failure_type),
        machine_id,
        max_points,
    )


@router.get("/maintenance-analysis")
async def maintenance_analysis(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
):
    return await _service(request).maintenance_analysis(
        _filters(start_date, end_date, machine_id, model, failure_type)
    )


@router.post("/analysis-context")
async def analysis_context(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    machine_id: int | None = Query(None, ge=1),
    model: str | None = None,
    failure_type: str | None = None,
):
    return await _service(request).analysis_context(
        _filters(start_date, end_date, machine_id, model, failure_type)
    )


@router.get("/index-audit")
async def index_audit(request: Request):
    """Read-only visibility; this endpoint never creates or changes an index."""

    return await _service(request).index_audit()
