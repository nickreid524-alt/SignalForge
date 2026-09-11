"""Everything the API needs, built once at startup and shared by the routes.

The MCP server is created in-process and the catalogue is read through the real MCP client, so the
tool and resource listings the browser sees are the server's own metadata rather than a hand-kept
copy that could drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from signalforge.api.config import ApiSettings
from signalforge.api.runner import InvestigationRunner
from signalforge.api.schemas import (
    IncidentDetail,
    IncidentSummary,
    McpCatalogue,
    ResourceSummary,
    ResourceTemplateSummary,
    ToolSummary,
)
from signalforge.audit.store import TraceStore
from signalforge.config import DATASET_LABEL, ENVIRONMENT_NAME, WorldConfig
from signalforge.events.store import EventStore
from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.server import create_server
from signalforge.world.generator import build_snapshot
from signalforge.world.repository import WorldRepository

CATALOGUE_NOTE = (
    "Read-only catalogue of the live MCP server. Tools are executed only by the investigation "
    "orchestrator; this API exposes no endpoint that calls an MCP tool on a caller's behalf."
)


@dataclass
class AppServices:
    settings: ApiSettings
    repo: WorldRepository
    server: Any
    trace: TraceStore
    events: EventStore
    runner: InvestigationRunner
    catalogue: McpCatalogue | None = None
    _owned: list[Any] = field(default_factory=list)

    # ------------------------------------------------------------------ construction
    @classmethod
    def build(cls, settings: ApiSettings | None = None, *, world: WorldConfig | None = None,
              snapshot: Any = None, trace: TraceStore | None = None, events: EventStore | None = None,
              provider_factory: Any = None) -> AppServices:
        settings = settings or ApiSettings.from_env()
        snapshot = snapshot if snapshot is not None else build_snapshot(world or WorldConfig())
        repo = WorldRepository(snapshot)
        server = create_server(snapshot=snapshot)
        trace = trace or TraceStore(settings.trace_db)
        events = events or EventStore(settings.event_db)
        runner_kwargs: dict[str, Any] = {}
        if provider_factory is not None:
            runner_kwargs["provider_factory"] = provider_factory
        runner = InvestigationRunner(
            server=server, trace=trace, events=events, max_concurrent=settings.max_concurrency,
            max_queued=settings.max_queued, allow_live_providers=settings.allow_live_providers, **runner_kwargs,
        )
        return cls(settings=settings, repo=repo, server=server, trace=trace, events=events, runner=runner)

    async def start(self) -> None:
        self.catalogue = await self._read_catalogue()

    async def aclose(self) -> None:
        await self.runner.aclose()

    # ------------------------------------------------------------------ MCP catalogue
    async def _read_catalogue(self) -> McpCatalogue:
        async with OpsClient.in_memory(self.server) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            return McpCatalogue(
                server_name=client.server_name, server_version=client.server_version,
                protocol_version=client.protocol_version, transport=client.transport, note=CATALOGUE_NOTE,
                tools=[ToolSummary(name=t.name, title=t.title, description=t.description,
                                   input_schema=t.input_schema, read_only=t.read_only, idempotent=t.idempotent)
                       for t in tools],
                resources=[ResourceSummary(uri=r.uri, name=r.name, mime_type=r.mime_type, description=r.description)
                           for r in resources],
                resource_templates=[ResourceTemplateSummary(uri_template=t.uri_template, name=t.name,
                                                            mime_type=t.mime_type, description=t.description)
                                    for t in templates],
            )

    # ------------------------------------------------------------------ world views
    @property
    def environment(self) -> str:
        return ENVIRONMENT_NAME

    @property
    def dataset_label(self) -> str:
        return DATASET_LABEL

    def incidents(self) -> list[IncidentSummary]:
        return [self._summary(i) for i in self.repo.open_incidents()]

    def incident(self, incident_id: str) -> IncidentDetail | None:
        found = self.repo.open_incident(incident_id)
        if found is None:
            return None
        return IncidentDetail(**self._summary(found).model_dump(), description=found.description)

    @staticmethod
    def _summary(incident: Any) -> IncidentSummary:
        """Only investigator-visible fields. The scenario catalogue is never imported here."""
        return IncidentSummary(
            id=incident.id, title=incident.title, severity=str(incident.severity),
            affected_service=incident.affected_service, detected_at=incident.detected_at.isoformat(),
            investigation_clock=incident.investigation_clock.isoformat(), reporter=incident.reporter,
        )
