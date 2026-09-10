"""Run the Synthetic Operations MCP Server.

    python -m signalforge.mcp_server                      # stdio (default; for hosts and the MCP Inspector)
    python -m signalforge.mcp_server --transport streamable-http --port 8000

Logging goes to stderr so stdout stays clean for the stdio protocol.
"""

from __future__ import annotations

import argparse
import logging
import sys

from signalforge.config import WorldConfig
from signalforge.mcp_server.server import create_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m signalforge.mcp_server", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=WorldConfig().seed)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    server = create_server(WorldConfig(seed=args.seed))
    if args.transport == "stdio":
        server.run()
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
