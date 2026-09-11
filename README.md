# SignalForge

**AI-Powered Operations Investigation** — work in progress (Phase 3: live model providers
behind a neutral boundary).

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
| 2 | Bounded investigation engine, scripted demo provider, grounded reports, audit trace, deterministic evaluation harness | done |
| 3 | Anthropic and OpenAI provider adapters behind the same boundary, error normalisation, token telemetry, cassette v2, live-run guards | done |
| 4 | Local HTTP API with a durable, replayable Server-Sent Events investigation stream | done (under review) |
| 5+ | Web UI | planned |

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

## Providers

Every run states `USES LIVE API: YES` or `NO`. The default provider is `scripted`, a
deterministic playbook that is explicitly **not** a language model; nothing in the tests, the
CI or the demo needs an API key or a network connection.

```bash
signalforge providers                                  # what each provider needs and whether it is ready
pip install -e ".[anthropic]"                          # or ".[openai]" or ".[providers]"
signalforge provider check anthropic --yes             # ONE tiny paid call to verify credentials
signalforge investigate INC-2026-0101 --provider anthropic --yes
signalforge eval --provider openai --scenario SCN-06 --yes
signalforge eval --provider openai --yes --allow-live-suite   # all 15 scenarios: many paid calls
```

| provider | USES LIVE API | needs |
|---|---|---|
| `scripted` | NO | nothing |
| `replay` | NO | a recorded cassette (`--cassette`) |
| `anthropic` | YES | `pip install -e ".[anthropic]"`, `ANTHROPIC_API_KEY`, `SIGNALFORGE_ANTHROPIC_MODEL` |
| `openai` | YES | `pip install -e ".[openai]"`, `OPENAI_API_KEY`, `SIGNALFORGE_OPENAI_MODEL` |

Model ids are never hardcoded; set them in the environment (see `.env.example`). Live runs
require `--yes`, print the provider, model and budget before calling anything, and a
multi-scenario live evaluation additionally requires `--allow-live-suite`.

**A Claude consumer subscription (such as Claude Max) is not Anthropic API access, and a ChatGPT
subscription is not OpenAI API access.** Both adapters need a per-token API key from the vendor's
developer platform. Keys never appear in traces, exports, cassettes, logs or error messages.

## Local API

```bash
signalforge serve                  # http://127.0.0.1:8765, scripted provider, no live API
signalforge api-demo               # drive the API over real HTTP and print a transcript
```

The API exposes the incident queue, the real MCP catalogue, provider status, investigations and
their evidence, hypotheses, reports and traces, plus a frozen benchmark. Live investigation progress
streams over Server-Sent Events at `/api/investigations/{id}/events`, with durable replay: a browser
that reconnects with `Last-Event-ID` receives exactly the events it missed.

It is a loopback developer application. The browser chooses an incident, a configured provider and a
named budget profile, and nothing else: no credentials, model ids, prompts, URLs or paths are
accepted, there is no endpoint that executes an MCP tool, and live providers stay disabled unless
the operator sets `SIGNALFORGE_API_ALLOW_LIVE=1`. CORS is closed by default and never a wildcard.
See `docs/notes/browser-trust-boundary.md`.

Technical notes live in `docs/notes/` (API boundary, SSE event contract, investigation runner,
browser trust boundary, provider architecture, the two adapters, the provider security boundary, the
cassette format, and the Phase 1–2 notes). The public README will be written once the web
UI exists.
