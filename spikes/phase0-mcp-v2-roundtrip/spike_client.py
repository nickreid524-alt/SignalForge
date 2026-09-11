"""Phase-0 spike client: in-memory and stdio round trips against spike_server."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import mcp
from mcp import Client, MCPError, StdioServerParameters
from mcp.types import TextContent, TextResourceContents

HERE = os.path.dirname(os.path.abspath(__file__))


def show(label, obj):
    print(f"\n=== {label} ===")
    print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str)[:1500])


async def exercise(client: Client, label: str):
    print(f"\n######## {label} ########")
    print("server_info:", client.server_info)
    print("protocol_version:", client.protocol_version)
    print("instructions:", client.instructions)

    tools = await client.list_tools()
    for t in tools.tools:
        show(f"tool {t.name}", {
            "title": t.title,
            "description": t.description,
            "annotations": t.annotations.model_dump(mode="json") if t.annotations else None,
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
        })

    r = await client.call_tool("query_logs", {"service": "checkout-api", "level": "ERROR"})
    show("call_tool query_logs ok", {"is_error": r.is_error, "structured_content": r.structured_content,
                                     "content": [c.text for c in r.content if isinstance(c, TextContent)]})

    r = await client.call_tool("query_logs", {"service": "unknown-svc"})
    show("call_tool ToolError", {"is_error": r.is_error, "structured_content": r.structured_content,
                                 "content": [c.text for c in r.content if isinstance(c, TextContent)]})

    r = await client.call_tool("query_logs", {"service": "checkout-api", "limit": 999})
    show("call_tool validation error (limit=999)", {"is_error": r.is_error,
                                                     "content": [c.text for c in r.content if isinstance(c, TextContent)]})

    r = await client.call_tool("get_service_health", {"service": "payments-gateway"})
    show("call_tool dict return", {"is_error": r.is_error, "structured_content": r.structured_content})

    try:
        r = await client.call_tool("does_not_exist", {})
        show("unknown tool", {"is_error": r.is_error, "content": [c.text for c in r.content if isinstance(c, TextContent)]})
    except MCPError as e:
        show("unknown tool -> MCPError", repr(e))

    res = await client.list_resources()
    show("list_resources", [x.model_dump(mode="json") for x in res.resources])
    tpl = await client.list_resource_templates()
    show("list_resource_templates", [x.model_dump(mode="json") for x in tpl.resource_templates])

    rr = await client.read_resource("catalog://services")
    show("read_resource catalog", [(c.mime_type, c.text) for c in rr.contents if isinstance(c, TextResourceContents)])
    rr = await client.read_resource("runbook://checkout-api/RB-001")
    show("read_resource template", [(c.mime_type, c.text) for c in rr.contents if isinstance(c, TextResourceContents)])
    try:
        await client.read_resource("runbook://checkout-api/RB-999")
    except MCPError as e:
        show("read_resource not found -> MCPError", f"code={e.error.code if hasattr(e, 'error') else '?'} {e}")


async def main():
    print("mcp version:", getattr(mcp, "__version__", "?"))
    sys.path.insert(0, HERE)
    from spike_server import mcp as server

    async with Client(server, raise_exceptions=True) as client:
        await exercise(client, "IN-MEMORY Client(server)")

    params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "spike_server.py")])
    async with Client(params) as client:
        await exercise(client, "STDIO subprocess")


if __name__ == "__main__":
    asyncio.run(main())
