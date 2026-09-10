"""Synthetic Operations MCP Server (read-only), built on the official ``mcp`` v2 SDK."""

from signalforge.mcp_server.server import SERVER_NAME, ServerContext, create_server

__all__ = ["SERVER_NAME", "ServerContext", "create_server"]
