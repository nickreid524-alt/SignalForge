# Spike: MCP Python SDK v2 round trip (Phase 0)

Throwaway validation, not project code. It exists to prove, before any
architecture was committed to, that the **official `mcp` v2 SDK** behaves the way
SignalForge's design assumes.

Run on 2026-09-10 with `mcp==2.2.0`, `mcp-types==2.2.0`, `pydantic==2.13.5`,
Python 3.12.10, Windows 11. Negotiated protocol version: `2026-07-28`.

## What it exercises

| Concern | v2 API used | Result |
|---|---|---|
| High-level server | `from mcp.server import MCPServer` | OK |
| Tool with structured output | `@mcp.tool()` returning a Pydantic model → `output_schema` auto-published, `structured_content` populated | OK |
| Tool with `dict[str, ...]` output | not wrapped in `{"result": ...}` | OK |
| Read-only annotations | `ToolAnnotations(read_only_hint=True, open_world_hint=False)` from `mcp.types` | OK, visible in `tools/list` |
| Argument validation before the handler runs | `Annotated[int, Field(ge=1, le=200)]`, called with `limit=999` | `is_error=True`, Pydantic message, handler never ran |
| Recoverable tool failure | `raise ToolError(...)` from `mcp.server.mcpserver.exceptions` | `is_error=True`, message in `content` |
| Unknown tool | `call_tool("does_not_exist")` | `is_error=True` result, **not** an exception (even with `raise_exceptions=True`) |
| Static resource | `@mcp.resource("catalog://services", mime_type="application/json")` returning `dict` | JSON text in `TextResourceContents` |
| Resource template with typed params | `@mcp.resource("runbook://{service}/{runbook_id}")` returning a Pydantic model | listed in `resources/templates/list`, read OK |
| Resource not found | `raise ResourceNotFoundError(...)` | `MCPError` code `-32602` on the client |
| In-memory transport for tests | `async with Client(server, raise_exceptions=True)` | OK, no subprocess, no port |
| stdio transport | `Client(StdioServerParameters(command=sys.executable, args=[...]))` | OK on Windows, identical results |

## Rerun

```bash
.venv/Scripts/python.exe spikes/phase0-mcp-v2-roundtrip/spike_client.py   # from the repo root
```

Captured output is in `output.txt`.
