# The local API boundary

Phase 4 puts an HTTP surface on the existing engine. The rule that shaped every decision: the API is
a **thin adapter**. It validates input, calls services that already existed, and shapes a response.
No investigation logic lives here.

```
browser ──HTTP──> signalforge.api ──> InvestigationRunner ──> Investigator (unchanged)
                        │                                          │
                        │                                          ├── ActionPolicy
                        ├── AppServices (world, MCP server,        ├── OpsClient ──MCP──> read-only server
                        │   trace store, event store)              ├── EvidenceRegistry
                        └── reads: events, trace, registry         ├── GroundingValidator
                                                                   └── TraceStore + EventEmitter
```

Dependency direction is one-way and enforced by a test: `signalforge.api` imports orchestration,
MCP, evidence, reports, audit and events; none of those imports `signalforge.api`. The API also
never imports `signalforge.scenarios` or `signalforge.evals`, so the browser stays on the
investigator's side of the ground-truth firewall (`tests/test_api_incidents.py`).

## Framework decision

**Starlette + Uvicorn + Pydantic, plus sse-starlette for the event stream.** No FastAPI.

The reasoning:

- All four were already installed as transitive dependencies of `mcp`, so this adds no new
  package to the environment. They are now declared as **direct** dependencies in `pyproject.toml`
  because SignalForge imports them directly, rather than relying on `mcp` to keep supplying them.
  `httpx2` is declared for the same reason: the API demo and the HTTP integration test use it.
- FastAPI's value is dependency injection, automatic OpenAPI and request-model binding. This API has
  eighteen endpoints, one request body and a validation layer that is deliberately explicit, so that
  value is small. Its cost is a large framework, a new major dependency, and a second opinionated way
  to express the same routing. Familiarity with it elsewhere is not a reason to add it here.
- Pydantic already models every domain object in the project, so request and response schemas reuse
  the models rather than introducing a second serialization stack.
- `sse-starlette` supplies `EventSourceResponse`: correct content type, keep-alive pings and client
  disconnect handling. Hand-rolling those is where SSE bugs live.

Versions verified on 2026-09-11: starlette 1.6.0, uvicorn 0.52.4, sse-starlette 3.4.11, httpx2 2.12.0.

Two Starlette details worth recording. `Starlette(max_body_size=...)` bounds request bodies at the
ASGI layer, so a large body never reaches a handler. And the request-id middleware is written as a
**pure ASGI class**, not `BaseHTTPMiddleware`, because the latter wraps streaming responses and
interferes with SSE disconnect detection.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness, version, whether live providers are enabled |
| GET | `/api/meta` | environment labels, MCP identity, counts, provider summaries, event catalogue |
| GET | `/api/incidents` | the 15 open incidents, investigator-visible fields only |
| GET | `/api/incidents/{incident_id}` | one incident, plus its description |
| GET | `/api/mcp/tools` | the real MCP tool catalogue with input schemas and read-only hints |
| GET | `/api/mcp/resources` | resources and resource templates |
| GET | `/api/providers` | per provider: mode, live flag, SDK, configured, model, ready, enabled |
| POST | `/api/investigations` | start one (202 Accepted, returns links) |
| GET | `/api/investigations` | everything this process has run |
| GET | `/api/investigations/{id}` | status, counters, hypotheses, validation, token and budget usage |
| GET | `/api/investigations/{id}/events` | **SSE** stream, replayable (see sse-event-contract.md) |
| GET | `/api/investigations/{id}/hypotheses` | the hypothesis board with full revision history |
| GET | `/api/investigations/{id}/evidence` | every evidence item, flagged untrusted |
| GET | `/api/investigations/{id}/evidence/{evidence_id}` | one item including payload and text |
| GET | `/api/investigations/{id}/report` | the canonical structured report, or 409 if not ready |
| GET | `/api/investigations/{id}/trace` | a safe, redacted projection of the audit trace |
| GET | `/api/evaluations` | which frozen benchmarks exist |
| GET | `/api/evaluations/{name}` | the frozen 15-scenario benchmark |

There is **no** endpoint that executes an MCP tool. Tool execution belongs to an investigation,
where the action policy, budgets, evidence registry and audit trace apply. A generic "call any tool"
endpoint would hand a browser the one capability the architecture exists to mediate.

There is also **no** endpoint that starts an evaluation. `/api/evaluations/scripted` serves a static
artifact generated offline by `signalforge eval`, so loading a page cannot start a 15-scenario run,
let alone a paid one.

## Requests

The only request body in the API:

```json
{"incident_id": "INC-2026-0114", "provider": "scripted", "budget_profile": "default"}
```

The model sets `extra="forbid"`, which is doing real security work: an API key, a model id, a base
URL, a system prompt, a tool list or a raw budget number in the body is rejected as an unknown field
before any handler runs. Budgets are chosen by name (`quick`, `default`, `thorough`) from a table in
`schemas.py`, never as numbers from the caller.

## Responses

Every response model lists its fields explicitly rather than dumping a domain object. That is what
stops a new field on a world or trace model from appearing in an HTTP response by accident, and it
is why the incident schema can be checked against the scenario answer key in a test.

## Report and trace

The report is returned as the canonical `InvestigationReport` JSON. Markdown is a rendering, never
the representation; a frontend can render it later. An investigation with no report yet answers 409
`report_not_ready` with its current status rather than an empty document that looks real.

The trace endpoint returns a projection built field by field in `api/trace_view.py`: status changes,
model calls (identity, latency, tokens, stop reason and error category, but no request or response
body), actions including every rejection, evidence acquisition, hypothesis updates, validation
rounds and repairs. Everything passes through the shared redaction helper again and through a path
scrubber. The UI therefore depends on this projection, not on SQLite column names, and the trace
schema can keep evolving.
