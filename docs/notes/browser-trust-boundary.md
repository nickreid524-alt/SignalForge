# The browser trust boundary

SignalForge's local API is a **loopback developer application**, not a public service. This note
states what that means precisely, because "it's only local" is not a security model on its own.

## What the browser is trusted to decide

Three things, all from closed sets:

| Input | Allowed values |
|---|---|
| `incident_id` | one of the 15 open incidents in the synthetic world |
| `provider` | `scripted` (default), or a live provider **if the operator enabled one** |
| `budget_profile` | `quick`, `default`, `thorough` |

## What the browser cannot decide

The request model forbids unknown fields, so each of these is a 400 before any handler runs:

- API keys or tokens of any kind
- a model id
- a provider base URL or any other URL
- a system prompt, user prompt or other prompt text
- a tool name, tool list, or MCP method
- raw budget numbers
- a filesystem path (trace database, cassette, export target)

And structurally:

- **No MCP execution endpoint exists.** The browser cannot call a tool. Tool execution happens
  inside an investigation, behind the action policy, the budget, the evidence registry and the
  audit trace. A generic "call any tool" endpoint would bypass the entire architecture.
- **No evaluation trigger exists.** `/api/evaluations/scripted` is a frozen artifact on disk.
  Loading a page cannot start a 15-scenario run.
- **No write-capable operational tools exist anywhere.** The MCP server is read-only, every tool
  carries `read_only_hint=True`, and Phase 4 added none.

## Network posture

| Setting | Default | Note |
|---|---|---|
| bind address | `127.0.0.1` | `serve` warns loudly if told to bind elsewhere |
| port | 8765 | |
| CORS origins | none | same-origin only; there is no frontend yet |
| live providers | disabled | `SIGNALFORGE_API_ALLOW_LIVE=1` to enable |
| request body | 16 KiB | enforced at the ASGI layer |

**CORS is never a wildcard.** `ApiSettings.from_env` strips `*` while parsing
`SIGNALFORGE_API_CORS_ORIGINS`, so a wildcard cannot reach the middleware even by misconfiguration,
and the middleware is only installed when an exact origin list is configured. When the web UI
arrives it gets its exact origin (for example `http://localhost:5173`) and nothing else. Credentials
are not allowed on cross-origin requests. A test asserts all of this.

The combination that would actually be dangerous, a wildcard origin plus enabled live providers,
is unreachable: the wildcard is dropped at parse time.

## Secrets

- The API never accepts a credential and never returns one.
- `/api/providers` answers `configured: true|false`. It does not return a value, a prefix, a
  length or a hash. A test asserts the response contains none of those.
- Setup errors name **environment variables**, never their values.
- Everything written to the event store and everything projected from the trace passes through
  `signalforge.redaction.redact`, and the trace projection additionally strips absolute filesystem
  paths so the operator's directory layout is not published.
- Provider opaque reasoning blocks are never persisted or exposed; see the provider security note.

## Untrusted content reaching the browser

Evidence is retrieved content from the synthetic environment, and three documents in that world
deliberately contain instructions aimed at an automated reader. The API returns them, because they
are evidence and a reviewer should see exactly what the investigator saw.

They are labelled rather than sanitised. Every evidence item carries `untrusted: true`, the
evidence endpoints carry a note saying so, and the trace projection repeats it. The defence against
those instructions is not filtering the text: it is that the orchestrator refused the actions they
asked for, and the refusals are visible as `tool.rejected` events and rejected actions in the trace.

A frontend consuming this API must render evidence text as **data**: escaped, never as HTML, and
never fed back into a prompt as an instruction.

## Ground truth

`signalforge.api` never imports `signalforge.scenarios` or `signalforge.evals`, enforced by a static
scan over the package. Incident responses list their fields explicitly and are tested against the
scenario answer key: no root cause, category, difficulty, fault handle, expected tool, red herring
or scenario id appears in anything the API serves about an incident. The frozen benchmark is the one
place expected categories legitimately appear, because a benchmark report is about results; it is
static data and unreachable from an investigation.

## What this is not

This is not an authenticated multi-tenant service. There is no login, no authorization, no rate
limiting beyond the concurrency and queue bounds, and no audit of *who* started an investigation.
Anyone who can reach the port can start scripted investigations. That is acceptable for a loopback
developer tool and would not be acceptable if it were exposed, which is why the default bind is
loopback and the `serve` banner warns when it is not.
