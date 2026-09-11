"""The open incident queue, as an investigator sees it.

This module never imports ``signalforge.scenarios`` or ``signalforge.evals``. The incident records in
the synthetic world carry no cause information by construction, and the response models list their
fields explicitly, so a future field on the world model cannot leak through by accident.
``tests/test_api_incidents.py`` asserts the serialized output against the scenario answer key.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.errors import not_found
from signalforge.api.routes.common import json_ok, services_of
from signalforge.api.schemas import valid_incident_id


async def list_incidents(request: Request) -> JSONResponse:
    services = services_of(request)
    return json_ok({"incidents": [i.model_dump(mode="json") for i in services.incidents()]})


async def get_incident(request: Request) -> JSONResponse:
    services = services_of(request)
    incident_id = valid_incident_id(request.path_params["incident_id"])
    incident = services.incident(incident_id)
    if incident is None:
        raise not_found("incident", incident_id)
    return json_ok(incident)
