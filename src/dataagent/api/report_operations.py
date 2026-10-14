from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request

from dataagent.api.bi import authorize
from dataagent.bi.contracts import Contract
from dataagent.report_operations import ActionReview, ActionSpec, ScheduleSpec

router = APIRouter(prefix="/api/report-operations", tags=["report-operations"])


class PauseRequest(Contract):
    paused: bool = True


class TriggerRequest(Contract):
    # Exclusive local calendar date; weekly Monday/monthly first day.
    period_end: date | None = None


@router.get("/status")
async def status(request: Request, agent_id: int = Depends(authorize)) -> Any:
    return request.app.state.report_operations.status()


@router.post("/schedules", status_code=201)
async def create_schedule(payload: ScheduleSpec, request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.create_schedule(agent_id, payload)


@router.get("/schedules")
async def schedules(request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.schedules(agent_id)


@router.post("/schedules/{schedule_id}/pause")
async def pause(
    schedule_id: str, payload: PauseRequest, request: Request, agent_id: int = Depends(authorize)
) -> Any:
    return await request.app.state.report_operations.set_paused(schedule_id, agent_id, payload.paused)


@router.post("/schedules/{schedule_id}/trigger", status_code=202)
async def trigger(
    schedule_id: str, payload: TriggerRequest, request: Request, agent_id: int = Depends(authorize)
) -> Any:
    return await request.app.state.report_operations.trigger(schedule_id, agent_id, payload.period_end)


@router.get("/runs")
async def runs(request: Request, schedule_id: str | None = None, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.runs(agent_id, schedule_id)


@router.get("/runs/{run_id}")
async def run(run_id: str, request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.run(run_id, agent_id)


@router.post("/runs/{run_id}/refresh")
async def refresh(run_id: str, request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.refresh_run(run_id, agent_id)


@router.post("/actions", status_code=201)
async def create_action(payload: ActionSpec, request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.create_action(agent_id, payload)


@router.get("/actions")
async def actions(request: Request, agent_id: int = Depends(authorize)) -> Any:
    return await request.app.state.report_operations.actions(agent_id)


@router.post("/actions/{action_id}/review")
async def review_action(
    action_id: str, payload: ActionReview, request: Request, agent_id: int = Depends(authorize)
) -> Any:
    return await request.app.state.report_operations.review_action(action_id, agent_id, payload)
