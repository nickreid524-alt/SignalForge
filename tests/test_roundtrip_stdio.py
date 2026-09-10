"""Real process isolation: the server runs as a subprocess and speaks MCP over stdio."""

from __future__ import annotations

import sys

import pytest

from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.tools import TOOL_NAMES

pytestmark = [pytest.mark.anyio, pytest.mark.stdio]


async def test_stdio_roundtrip_matches_in_memory(server):
    async with OpsClient.stdio(command=sys.executable) as remote, OpsClient.in_memory(server) as local:
        assert remote.transport == "stdio"
        assert remote.server_name == local.server_name == "signalforge-ops"
        assert remote.protocol_version == local.protocol_version

        remote_tools = await remote.list_tools()
        local_tools = await local.list_tools()
        assert [t.name for t in remote_tools] == sorted(TOOL_NAMES)
        assert [(t.name, t.input_schema, t.output_schema) for t in remote_tools] == \
               [(t.name, t.input_schema, t.output_schema) for t in local_tools]

        args = {"node": "payments", "as_of": "2026-08-14T00:28:00Z"}
        a = await remote.call_tool("get_service_health", args)
        b = await local.call_tool("get_service_health", args)
        assert a.ok and b.ok
        assert a.payload == b.payload  # same seed, same world, across a process boundary

        logs = await remote.call_tool("query_logs", {"node": "payments", "start": "2026-08-13T22:00:00Z",
                                                    "end": "2026-08-14T00:28:00Z", "contains": "x509", "limit": 3})
        assert logs.ok and logs.payload["total_matches"] > 0

        doc = await remote.read_resource("runbook://RB-016")
        assert doc.ok and doc.text.startswith("# Rotating the PayVault client certificate")
        missing = await remote.read_resource("runbook://RB-404")
        assert missing.ok is False

        templates = await remote.list_resource_templates()
        assert "topology://services/{service}" in [t.uri_template for t in templates]
