"""Small helpers every route uses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.errors import ApiError
from signalforge.api.services import AppServices


def services_of(request: Request) -> AppServices:
    return request.app.state.services


def json_ok(payload: BaseModel | dict[str, Any] | list[Any], status_code: int = 200) -> JSONResponse:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, list):
        payload = [p.model_dump(mode="json") if isinstance(p, BaseModel) else p for p in payload]
    return JSONResponse(payload, status_code=status_code)


async def json_body(request: Request) -> Any:
    """Parse a JSON body, mapping a malformed document or an oversized one to the error envelope."""
    try:
        return await request.json()
    except Exception:
        raise ApiError("invalid_request", "the request body must be valid JSON") from None
