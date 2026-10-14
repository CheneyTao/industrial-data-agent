from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from dataagent.api.graph import _authorize_agent
from dataagent.database import get_session

router = APIRouter(prefix="/api/chat-attachments", tags=["chat-attachments"])


@router.get("/capabilities")
async def capabilities(request: Request):
    return request.app.state.graph.attachments.capabilities()


@router.post("")
async def upload_attachment(
    request: Request,
    agent_id: int = Query(..., alias="agentId"),
    conversation_id: str = Query(..., alias="conversationId"),
    file: UploadFile = File(...),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_agent(agent_id, session, x_api_key, authorization)
    try:
        return await request.app.state.graph.attachments.upload(session, agent_id, conversation_id, file)
    finally:
        await file.close()


@router.get("/{attachment_id}")
async def attachment_preview(
    attachment_id: str,
    request: Request,
    agent_id: int = Query(..., alias="agentId"),
    conversation_id: str = Query(..., alias="conversationId"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_agent(agent_id, session, x_api_key, authorization)
    service = request.app.state.graph.attachments
    await service.authorize_session(session, agent_id, conversation_id)
    return service.load(agent_id, conversation_id, attachment_id)
