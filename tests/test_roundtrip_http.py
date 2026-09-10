"""Streamable HTTP spike: serve the MCPServer's ASGI app on a local port and connect with Client(url).

Kept as an integration test, not an application feature: SignalForge's architecture does not depend
on HTTP yet. Skipped automatically if uvicorn cannot bind a local port.
"""

from __future__ import annotations

import socket
import threading
import time

import anyio
import pytest

from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.server import create_server

pytestmark = [pytest.mark.anyio, pytest.mark.http]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def http_url(generated):
    uvicorn = pytest.importorskip("uvicorn")
    server = create_server(snapshot=generated.snapshot)
    app = server.streamable_http_app()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not uv.started and time.time() < deadline:
        time.sleep(0.05)
    if not uv.started:
        pytest.skip("uvicorn did not start on a local port")
    yield f"http://127.0.0.1:{port}/mcp"
    uv.should_exit = True
    thread.join(timeout=10)


async def test_streamable_http_roundtrip(http_url: str):
    with anyio.fail_after(30):
        async with OpsClient.http(http_url) as client:
            assert client.transport == "streamable-http"
            assert client.server_name == "signalforge-ops"
            tools = await client.list_tools()
            assert len(tools) == 9
            out = await client.call_tool("get_dependencies", {"service": "notifications"})
            assert out.ok and "EDGE-notifications-mailrelay" in out.payload["source_ids"]
            doc = await client.read_resource("incident://INC-2026-0044")
            assert doc.ok and "PayVault" in doc.text
