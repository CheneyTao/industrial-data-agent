from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from dataagent.documents import read_upload_limited
from dataagent.errors import DataAgentError, NotFoundError

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("/avatar")
async def upload_avatar(request: Request):
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise DataAgentError("file is required")
    content_type = getattr(upload, "content_type", None)
    if not content_type or not content_type.startswith("image/"):
        return JSONResponse(status_code=400, content={"success": False, "message": "只支持图片文件"})
    try:
        content = await read_upload_limited(upload, request.app.state.settings.file_image_size)
    except DataAgentError as exc:
        if exc.status_code != 413:
            raise
        return JSONResponse(status_code=400, content={"success": False, "message": "图片大小超过最大限制"})
    location, url = await request.app.state.file_storage.save(upload.filename or "avatar.bin", content)
    if request.app.state.settings.file_storage_type == "local":
        url = "/api/upload" + url
    return {"success": True, "message": "上传成功", "url": url, "filename": Path(location).name}


@router.get("/{file_path:path}")
async def get_upload(file_path: str, request: Request):
    settings = request.app.state.settings
    prefix = settings.file_url_prefix.strip("/")
    relative = file_path
    if prefix and relative.startswith(prefix + "/"):
        relative = relative[len(prefix) + 1 :]
    root = settings.file_path.resolve()
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise DataAgentError("Forbidden path", status_code=403)
    if not target.is_file():
        raise NotFoundError("File not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)
