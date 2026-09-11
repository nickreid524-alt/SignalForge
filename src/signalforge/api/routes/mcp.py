"""A read-only view of the real MCP server's catalogue.

There is deliberately no endpoint that executes an MCP tool. Tool execution belongs to an
investigation, where the action policy, the budget, the evidence registry and the audit trace apply.
A generic "call any tool" endpoint would hand a browser the one capability the whole architecture is
built to mediate.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.routes.common import json_ok, services_of


async def list_tools(request: Request) -> JSONResponse:
    catalogue = services_of(request).catalogue
    assert catalogue is not None, "catalogue is read during startup"
    return json_ok({
        "server_name": catalogue.server_name, "server_version": catalogue.server_version,
        "protocol_version": catalogue.protocol_version, "transport": catalogue.transport,
        "read_only": True, "note": catalogue.note,
        "tools": [t.model_dump(mode="json") for t in catalogue.tools],
    })


async def list_resources(request: Request) -> JSONResponse:
    catalogue = services_of(request).catalogue
    assert catalogue is not None, "catalogue is read during startup"
    return json_ok({
        "server_name": catalogue.server_name, "note": catalogue.note,
        "resources": [r.model_dump(mode="json") for r in catalogue.resources],
        "resource_templates": [t.model_dump(mode="json") for t in catalogue.resource_templates],
    })
