"""Provider availability, described without describing any credential."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.routes.common import json_ok, services_of
from signalforge.api.routes.system import provider_summaries

NOTE = (
    "A provider reports whether the server is configured, never what it is configured with. "
    "Live providers call paid vendor APIs and are disabled unless the operator enabled them at "
    "startup; a request cannot supply a key, a model or an endpoint."
)


async def list_providers(request: Request) -> JSONResponse:
    settings = services_of(request).settings
    return json_ok({
        "default": "scripted",
        "live_providers_enabled": settings.allow_live_providers,
        "note": NOTE,
        "providers": [p.model_dump(mode="json") for p in provider_summaries(settings.allow_live_providers)],
    })
