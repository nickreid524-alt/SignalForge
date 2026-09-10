"""Phase-0 spike: validate MCP Python SDK v2 API surface SignalForge will rely on.

Not project code. Exercises: MCPServer, @tool with Pydantic structured output,
ToolAnnotations(read_only_hint), argument validation, ToolError, static resource,
resource template with typed param, ResourceNotFoundError, stdio run.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations

mcp = MCPServer("spike-ops", instructions="Read-only synthetic ops environment (spike).")

_EVIDENCE_COUNTER = {"n": 0}


def _next_evidence_id() -> str:
    _EVIDENCE_COUNTER["n"] += 1
    return f"EVD-{_EVIDENCE_COUNTER['n']:06d}"


class LogRecord(BaseModel):
    timestamp: str
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    message: str


class LogQueryResult(BaseModel):
    evidence_id: str = Field(description="Stable evidence identifier for citation.")
    service: str
    total_matches: int
    records: list[LogRecord]
    truncated: bool = False


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def query_logs(
    service: Annotated[str, Field(description="Service name", min_length=1, max_length=64)],
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"] | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> LogQueryResult:
    """Search application logs for a service (synthetic)."""
    if service == "unknown-svc":
        raise ToolError(f"No such service: {service!r}")
    records = [
        LogRecord(timestamp="2026-09-10T08:01:00Z", level="ERROR", message="connection pool exhausted"),
        LogRecord(timestamp="2026-09-10T08:01:05Z", level="WARN", message="retrying upstream"),
    ]
    if level:
        records = [r for r in records if r.level == level]
    return LogQueryResult(
        evidence_id=_next_evidence_id(),
        service=service,
        total_matches=len(records),
        records=records[:limit],
    )


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def get_service_health(service: str) -> dict[str, str | float]:
    """Current health snapshot for a service (synthetic)."""
    return {"evidence_id": _next_evidence_id(), "service": service, "status": "degraded", "error_rate": 0.12}


@mcp.resource("catalog://services", mime_type="application/json")
def service_catalog() -> dict:
    """List of synthetic services."""
    return {"services": ["checkout-api", "payments-gateway"]}


class Runbook(BaseModel):
    runbook_id: str
    service: str
    title: str
    body: str


@mcp.resource("runbook://{service}/{runbook_id}", mime_type="application/json")
def runbook(service: str, runbook_id: str) -> Runbook:
    """A runbook document."""
    if runbook_id != "RB-001":
        raise ResourceNotFoundError(f"No runbook {runbook_id!r} for {service!r}")
    return Runbook(runbook_id=runbook_id, service=service, title="Pool exhaustion", body="1. Check pool metrics...")


@mcp.resource("incident://{incident_id}")
def incident(incident_id: str) -> str:
    """Historical incident report (text)."""
    return f"INCIDENT {incident_id}: checkout latency regression after deploy."


if __name__ == "__main__":
    mcp.run()  # stdio
