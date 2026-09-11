"""The SignalForge local API.

Starlette + Uvicorn + Pydantic, and nothing larger. The application layer is thin on purpose: routes
validate input, call the existing services, and shape a response. The investigation loop, the MCP
boundary, the evidence registry, the grounding validator and the audit trace all live where they
already lived; nothing is reimplemented here, and the orchestration and domain packages never import
this one.

Trust model in one paragraph: this is a local developer application bound to loopback. The browser
may choose an incident, a configured provider and a named budget profile. It may not supply
credentials, model ids, prompts, URLs, file paths, tool names or raw budget numbers, and there is no
endpoint that executes an MCP tool on its behalf. See docs/notes/browser-trust-boundary.md.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from signalforge.api.config import ApiSettings
from signalforge.api.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unhandled_error_handler,
)
from signalforge.api.routes import (
    evaluations,
    evidence,
    incidents,
    investigations,
    mcp,
    providers,
    system,
)
from signalforge.api.services import AppServices

API = "/api"


class RequestContextMiddleware:
    """Pure-ASGI request id. Deliberately not BaseHTTPMiddleware, which interferes with SSE."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["request_id"] = uuid.uuid4().hex[:16]
        await self.app(scope, receive, send)


def routes() -> list[Route]:
    return [
        Route(f"{API}/health", system.health, methods=["GET"]),
        Route(f"{API}/meta", system.meta, methods=["GET"]),
        Route(f"{API}/incidents", incidents.list_incidents, methods=["GET"]),
        Route(f"{API}/incidents/{{incident_id}}", incidents.get_incident, methods=["GET"]),
        Route(f"{API}/mcp/tools", mcp.list_tools, methods=["GET"]),
        Route(f"{API}/mcp/resources", mcp.list_resources, methods=["GET"]),
        Route(f"{API}/providers", providers.list_providers, methods=["GET"]),
        Route(f"{API}/investigations", investigations.list_investigations, methods=["GET"]),
        Route(f"{API}/investigations", investigations.create_investigation, methods=["POST"]),
        Route(f"{API}/investigations/{{investigation_id}}", investigations.get_investigation, methods=["GET"]),
        Route(f"{API}/investigations/{{investigation_id}}/events", investigations.stream_events, methods=["GET"]),
        Route(f"{API}/investigations/{{investigation_id}}/hypotheses", investigations.get_hypotheses, methods=["GET"]),
        Route(f"{API}/investigations/{{investigation_id}}/report", investigations.get_report, methods=["GET"]),
        Route(f"{API}/investigations/{{investigation_id}}/trace", investigations.get_trace, methods=["GET"]),
        Route(f"{API}/investigations/{{investigation_id}}/evidence", evidence.list_evidence, methods=["GET"]),
        Route(f"{API}/investigations/{{investigation_id}}/evidence/{{evidence_id}}", evidence.get_evidence,
              methods=["GET"]),
        Route(f"{API}/evaluations", evaluations.list_evaluations, methods=["GET"]),
        Route(f"{API}/evaluations/{{name}}", evaluations.get_evaluation, methods=["GET"]),
    ]


def middleware(settings: ApiSettings) -> list[Middleware]:
    stack = [Middleware(RequestContextMiddleware)]
    if settings.cors_origins:
        # Exact origins only. ApiSettings drops "*" when reading the environment, so a wildcard
        # cannot reach this call even by misconfiguration.
        stack.append(Middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"],
            allow_headers=["content-type", "last-event-id"], allow_credentials=False, max_age=600,
        ))
    return stack


def create_app(*, settings: ApiSettings | None = None, services: AppServices | None = None) -> Starlette:
    """Build the ASGI app. Pass ``services`` to supply prebuilt stores (tests, embedded use)."""
    settings = settings or (services.settings if services else ApiSettings.from_env())

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        built = services or AppServices.build(settings)
        app.state.services = built
        app.state.settings = settings
        await built.start()
        try:
            yield
        finally:
            await built.aclose()

    return Starlette(
        routes=routes(),
        middleware=middleware(settings),
        exception_handlers={
            ApiError: api_error_handler,
            HTTPException: http_error_handler,
            Exception: unhandled_error_handler,
        },
        lifespan=lifespan,
        max_body_size=settings.max_body_bytes,
    )
