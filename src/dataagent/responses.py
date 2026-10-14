from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def ok(data: Any = None, message: str = "success", *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"success": True, "message": message, "data": data}),
    )


def fail(message: str, *, status_code: int = 400, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"success": False, "message": message, "data": data}),
    )


def page(
    records: list[Any], *, total: int, page_num: int, page_size: int, message: str = "success"
) -> JSONResponse:
    pages = (total + page_size - 1) // page_size if page_size else 0
    return JSONResponse(
        content=jsonable_encoder(
            {
                "success": True,
                "message": message,
                "data": records,
                "total": total,
                "pageNum": page_num,
                "pageSize": page_size,
                "totalPages": pages,
            }
        )
    )
