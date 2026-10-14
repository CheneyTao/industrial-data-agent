from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.api.graph import _authorize_agent
from dataagent.database import get_session
from dataagent.diagnostics import DiagnosisRequest, DiagnosticsService

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.post("")
async def diagnose(
    payload: DiagnosisRequest,
    request: Request,
    x_api_key: str | None = Header(None),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Any:
    await _authorize_agent(payload.agent_id, session, x_api_key, authorization)
    return DiagnosticsService(request.app.state.graph.artifacts).diagnose(payload)
