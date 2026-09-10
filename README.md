# SignalForge

**AI-Powered Operations Investigation** — work in progress (Phase 1: foundation and
real MCP operations server).

SignalForge investigates operational incidents by gathering evidence from a
**Synthetic Operations Environment** ("SignalForge Demo Commerce") through
tools and resources exposed by a read-only server built on the official
[Model Context Protocol](https://modelcontextprotocol.io) Python SDK v2.

All data in this repository is synthetic. Services, deployments, logs,
metrics, alerts, runbooks and incidents are generated deterministically from a
fixed seed and describe a fictional company. Nothing here is drawn from any
real employer, customer, system or incident.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Architecture and MCP SDK v2 validation | done |
| 1 | Synthetic world, MCP server + resources, retrieval, evidence registry, MCP client | done |
| 2 | Bounded investigation engine, scripted demo provider, grounded reports, audit trace, deterministic evaluation harness | done (under review) |
| 3+ | Live providers (Anthropic/OpenAI behind the same boundary), API, web UI | planned |

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Windows; use .venv/bin/pip on POSIX
pytest
signalforge demo                          # engineering demonstration of the MCP boundary (no AI)
signalforge investigate INC-2026-0101     # SCRIPTED DEMONSTRATION MODE: bounded investigation, no LLM API
signalforge trace --list                  # persisted investigation traces (runs/signalforge.sqlite)
signalforge eval                          # deterministic evaluation of all 15 scenarios
signalforge serve-mcp                     # stdio MCP server, e.g. for the MCP Inspector
```

Technical notes for Phase 1 live in `docs/notes/`. The public README
will be written once the investigation loop and evaluations exist.
