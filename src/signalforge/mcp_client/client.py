"""OpsClient: the only place SignalForge touches ``mcp.Client``.

Everything returned to the rest of the application is a plain SignalForge
dataclass; SDK types (``CallToolResult``, ``Tool``, ``MCPError``) do not leak.
Failures are normalised into outcomes rather than exceptions so the caller can
register them as (failed) evidence and keep going.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal

from mcp import Client, MCPError, StdioServerParameters
from mcp.types import TextContent, TextResourceContents

from signalforge.evidence.models import EvidenceItem
from signalforge.evidence.registry import EvidenceRegistry

ErrorKind = Literal["tool_error", "protocol_error", "timeout", "transport"]
TIMEOUT_CODE = -32001


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool | None
    idempotent: bool | None


@dataclass(frozen=True)
class ResourceDescriptor:
    uri: str
    name: str
    mime_type: str | None
    description: str | None


@dataclass(frozen=True)
class ResourceTemplateDescriptor:
    uri_template: str
    name: str
    mime_type: str | None
    description: str | None


@dataclass(frozen=True)
class ResourceDocument:
    uri: str
    ok: bool
    mime_type: str | None
    text: str | None
    payload: dict[str, Any] | None
    source_ids: list[str]
    error: str | None
    latency_ms: float


@dataclass(frozen=True)
class ToolCallOutcome:
    name: str
    arguments: dict[str, Any]
    ok: bool
    payload: dict[str, Any] | None
    text: str
    error: str | None
    error_kind: ErrorKind | None
    latency_ms: float
    source_ids: list[str] = field(default_factory=list)


def source_ids_from_uri(uri: str, payload: dict[str, Any] | None) -> list[str]:
    if isinstance(payload, dict) and isinstance(payload.get("source_ids"), list):
        return [s for s in payload["source_ids"] if isinstance(s, str)]
    scheme, _, rest = uri.partition("://")
    if scheme in ("runbook", "incident") and rest:
        return [rest]
    return [uri]


def _mcp_error_code(exc: MCPError) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


class OpsClient:
    def __init__(self, sdk_client: Client, transport: str) -> None:
        self._client = sdk_client
        self.transport = transport

    # ------------------------------------------------------------------ construction
    @classmethod
    @asynccontextmanager
    async def in_memory(cls, server: Any, *, raise_exceptions: bool = True) -> AsyncIterator[OpsClient]:
        """Connect to an ``MCPServer`` object in-process (tests, embedded mode)."""
        async with Client(server, raise_exceptions=raise_exceptions) as sdk_client:
            yield cls(sdk_client, "in-memory")

    @classmethod
    @asynccontextmanager
    async def stdio(cls, command: str | None = None, args: list[str] | None = None,
                    env: Mapping[str, str] | None = None) -> AsyncIterator[OpsClient]:
        """Spawn the server as a subprocess and speak MCP over stdio (real process isolation)."""
        params = StdioServerParameters(
            command=command or sys.executable,
            args=args if args is not None else ["-m", "signalforge.mcp_server", "--transport", "stdio"],
            env=dict(env) if env else None,
        )
        async with Client(params) as sdk_client:
            yield cls(sdk_client, "stdio")

    @classmethod
    @asynccontextmanager
    async def http(cls, url: str) -> AsyncIterator[OpsClient]:
        """Connect to a running Streamable HTTP server (e.g. http://127.0.0.1:8000/mcp)."""
        async with Client(url) as sdk_client:
            yield cls(sdk_client, "streamable-http")

    # ------------------------------------------------------------------ identity
    @property
    def server_name(self) -> str | None:
        info = self._client.server_info
        return getattr(info, "name", None) if info else None

    @property
    def server_version(self) -> str | None:
        info = self._client.server_info
        return getattr(info, "version", None) if info else None

    @property
    def protocol_version(self) -> str | None:
        return getattr(self._client, "protocol_version", None)

    @property
    def instructions(self) -> str | None:
        return getattr(self._client, "instructions", None)

    # ------------------------------------------------------------------ discovery
    async def list_tools(self) -> list[ToolDescriptor]:
        result = await self._client.list_tools()
        out: list[ToolDescriptor] = []
        for tool in result.tools:
            ann = tool.annotations
            out.append(ToolDescriptor(
                name=tool.name, title=tool.title, description=tool.description or "",
                input_schema=dict(tool.input_schema or {}),
                output_schema=dict(tool.output_schema) if tool.output_schema else None,
                read_only=getattr(ann, "read_only_hint", None) if ann else None,
                idempotent=getattr(ann, "idempotent_hint", None) if ann else None,
            ))
        return sorted(out, key=lambda t: t.name)

    async def list_resources(self) -> list[ResourceDescriptor]:
        result = await self._client.list_resources()
        return sorted(
            (ResourceDescriptor(uri=str(r.uri), name=r.name, mime_type=r.mime_type, description=r.description)
             for r in result.resources),
            key=lambda r: r.uri,
        )

    async def list_resource_templates(self) -> list[ResourceTemplateDescriptor]:
        result = await self._client.list_resource_templates()
        return sorted(
            (ResourceTemplateDescriptor(uri_template=t.uri_template, name=t.name, mime_type=t.mime_type,
                                        description=t.description) for t in result.resource_templates),
            key=lambda t: t.uri_template,
        )

    # ------------------------------------------------------------------ reads
    async def read_resource(self, uri: str) -> ResourceDocument:
        started = perf_counter()
        try:
            result = await self._client.read_resource(uri)
        except MCPError as exc:
            return ResourceDocument(uri=uri, ok=False, mime_type=None, text=None, payload=None, source_ids=[],
                                    error=f"protocol_error {_mcp_error_code(exc)}: {exc}",
                                    latency_ms=_elapsed(started))
        except Exception as exc:  # transport-level failure
            return ResourceDocument(uri=uri, ok=False, mime_type=None, text=None, payload=None, source_ids=[],
                                    error=f"transport: {exc.__class__.__name__}: {exc}", latency_ms=_elapsed(started))
        texts = [c for c in result.contents if isinstance(c, TextResourceContents)]
        if not texts:
            return ResourceDocument(uri=uri, ok=False, mime_type=None, text=None, payload=None, source_ids=[],
                                    error="resource returned no text content", latency_ms=_elapsed(started))
        first = texts[0]
        text = "\n".join(c.text for c in texts)
        payload: dict[str, Any] | None = None
        if (first.mime_type or "").startswith("application/json"):
            try:
                loaded = json.loads(text)
                payload = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                payload = None
        return ResourceDocument(uri=uri, ok=True, mime_type=first.mime_type, text=text, payload=payload,
                                source_ids=source_ids_from_uri(uri, payload), error=None, latency_ms=_elapsed(started))

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                        timeout: float = 30.0) -> ToolCallOutcome:
        arguments = dict(arguments or {})
        started = perf_counter()
        try:
            result = await self._client.call_tool(name, arguments, read_timeout_seconds=timeout)
        except MCPError as exc:
            code = _mcp_error_code(exc)
            kind: ErrorKind = "timeout" if code == TIMEOUT_CODE else "protocol_error"
            return ToolCallOutcome(name, arguments, False, None, "", f"{kind} {code}: {exc}", kind, _elapsed(started))
        except Exception as exc:  # transport-level failure
            return ToolCallOutcome(name, arguments, False, None, "", f"transport: {exc.__class__.__name__}: {exc}",
                                   "transport", _elapsed(started))
        text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        if result.is_error:
            return ToolCallOutcome(name, arguments, False, None, text, text or "tool error", "tool_error",
                                   _elapsed(started))
        payload = result.structured_content if isinstance(result.structured_content, dict) else None
        source_ids = [s for s in payload.get("source_ids", [])] if payload and isinstance(payload.get("source_ids"), list) else []
        return ToolCallOutcome(name, arguments, True, payload, text, None, None, _elapsed(started), source_ids)

    # ------------------------------------------------------------------ evidence
    async def gather(self, registry: EvidenceRegistry, name: str, arguments: dict[str, Any] | None = None,
                     timeout: float = 30.0) -> EvidenceItem:
        outcome = await self.call_tool(name, arguments, timeout=timeout)
        return registry.register_tool_result(
            name=name, arguments=outcome.arguments, payload=outcome.payload, text=outcome.text or None,
            ok=outcome.ok, error=outcome.error, latency_ms=outcome.latency_ms,
        )

    async def read_as_evidence(self, registry: EvidenceRegistry, uri: str) -> EvidenceItem:
        doc = await self.read_resource(uri)
        return registry.register_resource(
            uri=uri, text=doc.text, payload=doc.payload, source_ids=doc.source_ids, ok=doc.ok, error=doc.error,
            latency_ms=doc.latency_ms,
        )


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
