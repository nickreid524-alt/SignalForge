"""Health and metadata. Safe facts only: no environment dump, no paths, no credentials."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge import __version__
from signalforge.api.routes.common import json_ok, services_of
from signalforge.api.schemas import Health, Meta, ProviderSummary
from signalforge.events.models import EventType
from signalforge.providers.factory import all_provider_statuses, default_provider_name


def provider_summaries(allow_live: bool) -> list[ProviderSummary]:
    """Provider readiness with no description of the credential itself.

    ``configured`` is a boolean and nothing more: no value, prefix, length or hash of a key is
    computed anywhere in this file, so none can be returned.
    """
    summaries: list[ProviderSummary] = []
    for status in all_provider_statuses():
        enabled = status.name in ("scripted",) or (not status.uses_live_api) or allow_live
        summaries.append(ProviderSummary(
            name=status.name, mode=status.mode, uses_live_api=status.uses_live_api,
            sdk_installed=status.sdk_installed, sdk_version=status.sdk_version,
            configured=status.credentials_present, model_configured=bool(status.model), model=status.model,
            ready=status.ready, enabled=enabled,
            note=status.note if enabled else f"{status.note}; disabled on this server",
        ))
    return summaries


async def health(request: Request) -> JSONResponse:
    services = services_of(request)
    return json_ok(Health(version=__version__, uses_live_api=services.settings.allow_live_providers))


async def meta(request: Request) -> JSONResponse:
    services = services_of(request)
    catalogue = services.catalogue
    settings = services.settings
    return json_ok(Meta(
        version=__version__,
        environment=services.environment,
        dataset_label=services.dataset_label,
        mode="scripted demonstration" if not settings.allow_live_providers else "live providers enabled",
        default_provider=default_provider_name(),
        live_providers_enabled=settings.allow_live_providers,
        uses_live_api=settings.allow_live_providers,
        mcp_server_name=catalogue.server_name if catalogue else None,
        mcp_server_version=catalogue.server_version if catalogue else None,
        mcp_protocol_version=catalogue.protocol_version if catalogue else None,
        incident_count=len(services.repo.open_incidents()),
        tool_count=len(catalogue.tools) if catalogue else 0,
        resource_count=len(catalogue.resources) if catalogue else 0,
        resource_template_count=len(catalogue.resource_templates) if catalogue else 0,
        providers=provider_summaries(settings.allow_live_providers),
        event_types=[t.value for t in EventType],
    ))
