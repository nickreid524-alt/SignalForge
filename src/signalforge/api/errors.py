"""One JSON error envelope for every failure, and nothing else.

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Stack traces stay in the local log. The browser gets a stable code, a sentence, and the request id
that ties its report to that log line.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.redaction import redact

logger = logging.getLogger("signalforge.api")

#: Stable error codes. The message varies; the code does not.
CODES: dict[str, int] = {
    "not_found": 404,
    "invalid_request": 400,
    "provider_unavailable": 409,
    "investigation_conflict": 409,
    "report_not_ready": 409,
    "too_many_investigations": 429,
    "method_not_allowed": 405,
    "internal_error": 500,
}


class ApiError(Exception):
    """A failure with a code the frontend may branch on."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        if code not in CODES:
            raise KeyError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def status_code(self) -> int:
        return CODES[self.code]


def not_found(what: str, identifier: str) -> ApiError:
    return ApiError("not_found", f"no {what} with id {identifier!r}")


def envelope(code: str, message: str, request_id: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": redact(message) or message, "request_id": request_id}
    if details:
        error["details"] = details
    return {"error": error}


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]


def error_response(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(envelope(code, message, request_id_of(request), details), status_code=CODES[code])


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(request, exc.code, exc.message, exc.details)


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = {404: "not_found", 405: "method_not_allowed", 400: "invalid_request"}.get(exc.status_code, "internal_error")
    return error_response(request, code, exc.detail or "request failed")


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail locally; tell the browser only that something broke, with the request id."""
    request_id = request_id_of(request)
    logger.exception("unhandled error handling %s %s (request_id=%s)", request.method, request.url.path, request_id)
    return JSONResponse(
        envelope("internal_error", "the request could not be completed; see the server log", request_id),
        status_code=500,
    )
