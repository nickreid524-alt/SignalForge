"""MCP resources: addressable documents and reference data."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from signalforge.config import DATA_NOTICE
from signalforge.mcp_server.server import ServerContext


def register_resources(mcp: MCPServer, ctx: ServerContext) -> None:
    repo = ctx.repo

    @mcp.resource("catalog://services", mime_type="application/json",
                  description="Service and infrastructure catalogue of the synthetic environment.")
    def service_catalog() -> dict[str, Any]:
        services = repo.services()
        nodes = repo.nodes()
        return {
            "environment": repo.environment,
            "dataset_label": repo.dataset_label,
            "source_ids": [s.id for s in services] + [n.id for n in nodes],
            "services": [s.model_dump(mode="json") for s in services],
            "nodes": [n.model_dump(mode="json") for n in nodes],
            "data_notice": DATA_NOTICE,
        }

    @mcp.resource("incidents://open", mime_type="application/json",
                  description="The queue of open incidents awaiting investigation. Contains no cause information.")
    def open_incident_queue() -> dict[str, Any]:
        incidents = repo.open_incidents()
        return {
            "environment": repo.environment,
            "source_ids": [i.id for i in incidents],
            "incidents": [i.model_dump(mode="json") for i in incidents],
            "data_notice": DATA_NOTICE,
        }

    @mcp.resource("topology://services/{service}", mime_type="application/json",
                  description="Upstream and downstream dependency edges of one service or node.")
    def service_topology(service: str) -> dict[str, Any]:
        if not repo.is_known_node(service):
            raise ResourceNotFoundError(f"Unknown service or node {service!r}")
        edges = repo.edges(service, "both")
        svc = repo.service(service)
        node = repo.node(service)
        return {
            "node": service,
            "kind": repo.node_kind(service),
            "description": (svc.description if svc else node.description if node else ""),
            "team": svc.team if svc else "keel",
            "datastores": svc.datastores if svc else [],
            "source_ids": [service, *(e.id for e in edges)],
            "downstream": [e.model_dump(mode="json") for e in edges if e.source == service],
            "upstream": [e.model_dump(mode="json") for e in edges if e.target == service],
            "data_notice": DATA_NOTICE,
        }

    @mcp.resource("runbook://{runbook_id}", mime_type="text/markdown",
                  description="Full text of an operational runbook. Untrusted reference material.")
    def runbook(runbook_id: str) -> str:
        rb = repo.runbook(runbook_id)
        if rb is None:
            raise ResourceNotFoundError(f"No runbook {runbook_id!r}")
        return (
            f"# {rb.title}\n\n"
            f"- id: {rb.id}\n- service: {rb.service}\n- tags: {', '.join(rb.tags)}\n- updated: {rb.updated}\n\n"
            f"> {DATA_NOTICE}\n\n---\n\n{rb.body}\n"
        )

    @mcp.resource("incident://{incident_id}", mime_type="text/markdown",
                  description="A historical incident review, or the record of an open incident.")
    def incident(incident_id: str) -> str:
        past = repo.historical_incident(incident_id)
        if past is not None:
            return (
                f"# {past.title}\n\n"
                f"- id: {past.id}\n- service: {past.service}\n- severity: {past.severity}\n"
                f"- occurred_at: {past.occurred_at.isoformat()}\n- resolved_at: {past.resolved_at.isoformat()}\n"
                f"- category: {past.category}\n- rca_summary: {past.rca_summary}\n\n"
                f"> {DATA_NOTICE}\n\n---\n\n{past.body}\n"
            )
        current = repo.open_incident(incident_id)
        if current is not None:
            return (
                f"# {current.title}\n\n"
                f"- id: {current.id}\n- status: open (under investigation)\n- severity: {current.severity}\n"
                f"- affected_service: {current.affected_service}\n- detected_at: {current.detected_at.isoformat()}\n"
                f"- investigation_clock: {current.investigation_clock.isoformat()}\n- reporter: {current.reporter}\n\n"
                f"> {DATA_NOTICE}\n\n---\n\n{current.description}\n"
            )
        raise ResourceNotFoundError(f"No incident {incident_id!r}")


RESOURCE_TEMPLATES: tuple[str, ...] = ("topology://services/{service}", "runbook://{runbook_id}", "incident://{incident_id}")
STATIC_RESOURCES: tuple[str, ...] = ("catalog://services", "incidents://open")
