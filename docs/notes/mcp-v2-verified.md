# MCP Python SDK v2 — versions and API verified in Phase 1

**Verified 2026-09-10** on Windows 11, CPython 3.12.10:

| Package | Version | Role |
|---|---|---|
| `mcp` | **2.2.0** | official SDK: `MCPServer`, `Client`, transports |
| `mcp-types` | 2.2.0 | protocol types (exact-pinned by `mcp`) |
| `pydantic` | 2.13.5 | declared directly; all SignalForge schemas |
| protocol | `2026-07-28` | negotiated by `Client(...)` in tests and the demo |

Pins in `pyproject.toml`: `mcp>=2.2,<3`, `pydantic>=2.12,<3`. The full resolved
environment is recorded in `constraints.txt` for reproducible installs
(`pip install -c constraints.txt -e ".[dev]"`).

## API actually used

Server (`src/signalforge/mcp_server/`):

- `from mcp.server import MCPServer`; `MCPServer(name, title=..., instructions=..., version=...)`.
- `@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, destructive_hint=False, open_world_hint=False))`.
  Arguments are typed with `Annotated[..., Field(...)]`; the SDK publishes `inputSchema` and rejects
  invalid arguments before the handler runs. Return types are Pydantic models, so the SDK publishes
  `outputSchema` and fills `structured_content`; tests validate every result against its own published schema.
- `raise ToolError(...)` (`mcp.server.mcpserver.exceptions`) for recoverable failures → `is_error=True` result.
- `@mcp.resource("catalog://services", mime_type="application/json")` for a static resource and
  RFC 6570 templates `topology://services/{service}`, `runbook://{runbook_id}`, `incident://{incident_id}`;
  `raise ResourceNotFoundError(...)` → JSON-RPC error `-32602`.
- `mcp.run()` (stdio) and `mcp.run(transport="streamable-http", host=..., port=...)`; `mcp.streamable_http_app()`
  is used by the HTTP integration test under uvicorn.

Client (`src/signalforge/mcp_client/client.py`):

- `Client(server_object, raise_exceptions=True)` — in-memory; `Client(StdioServerParameters(command, args))` — stdio subprocess;
  `Client("http://127.0.0.1:<port>/mcp")` — Streamable HTTP.
- `await client.list_tools()` (`tool.input_schema`, `tool.output_schema`, `tool.annotations.read_only_hint`),
  `await client.call_tool(name, arguments, read_timeout_seconds=...)`, `list_resources()`, `list_resource_templates()`,
  `read_resource(uri)`; `client.server_info`, `client.protocol_version`, `client.instructions`.
- `MCPError` exposes `.code` and `.message` directly (there is no nested `.error`); timeouts use code `-32001`.

## Behaviours that shaped the code

- **Tool failures, argument-validation failures and unknown tool names are all `is_error=True` results**, not
  exceptions — even with `raise_exceptions=True`. `OpsClient` therefore never relies on exceptions for tool
  outcomes and normalises everything into `ToolCallOutcome`.
- **Sync handlers run on worker threads.** The FTS index guards its SQLite connection with a lock and
  `check_same_thread=False`; the world repository is immutable in-memory data.
- `Client.call_tool` takes `read_timeout_seconds`, not `timeout` (the docs' migration page describes
  `ClientSession`; the high-level `Client` differs). Discovered by the Phase 1 demo; fixed in the wrapper.
- `mcp.__version__` does not exist; use `importlib.metadata.version("mcp")`.
- The server's `instructions` string is a constant built from configuration, never from corpus text
  (asserted by `tests/test_security.py`).
