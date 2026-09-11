# The investigation runner and its concurrency model

`signalforge.api.runner.InvestigationRunner` answers one question: how does an HTTP request start
work that outlives it, without the server losing track of that work?

## What it does and does not do

It creates a provider through the existing factory, opens an MCP client, constructs the existing
`Investigator`, and awaits it. That is all. There is no second agent loop, no duplicated policy, no
reimplemented budget. If the orchestration logic changed tomorrow, this file would not.

The one addition made to the engine for this phase is a single optional callback,
`Investigator.run(..., on_state=...)`, which hands the caller the live `InvestigationState` object
the moment it is created. That is what lets `GET /api/investigations/{id}` report real progress
while a run is still going, instead of only after it finishes.

## Lifecycle

```
POST /api/investigations
  └─ validate body                      400 on an unknown field
  └─ resolve the incident               404 if it is not in the queue
  └─ build the provider eagerly         409 provider_unavailable on a misconfiguration
  └─ check the queue bound              429 too_many_investigations
  └─ record + emit investigation.created
  └─ create an asyncio task             202 Accepted, immediately
```

Creating the provider **before** returning 202 is deliberate: a missing key or SDK is a synchronous
409 the caller can act on, not a background task that fails a second later for reasons the caller
never sees.

Every task is held in a set. `aclose()`, called from the app's lifespan shutdown, cancels them all
and awaits them, so no investigation outlives the process that started it. A cancelled run records
the reason and emits a terminal `investigation.failed`, so a stream watching it still closes. A test
asserts the task set is empty after shutdown.

## Concurrency

Two bounds, both conservative by default:

| Setting | Default | Meaning |
|---|---|---|
| `SIGNALFORGE_API_MAX_CONCURRENCY` | 2 | investigations executing at once (clamped to 1–8) |
| `SIGNALFORGE_API_MAX_QUEUED` | 16 | queued plus running before new requests are refused (1–256) |

An `asyncio.Semaphore` enforces the first. The runner counts `active` and records
`peak_concurrency`, which is what the tests assert against: three slow investigations with a limit
of two reach a peak of exactly two, never three, and drop back to zero; with a limit of one the peak
is one. Exceeding the queue bound returns 429 rather than accepting unbounded work.

Why two and not more: each investigation holds an MCP client and, with a live provider, makes paid
API calls. A local developer application should not surprise its operator by running eight at once.

## Status while running

The record merges the runner's own view (queued, started, ended, error) with the live domain state
(status, usage, hypotheses, evidence, validation). Before the engine has built its state the API
reports `queued`; after that it reports the real state-machine status, which is why the status
document is useful during a run and not only at the end.

## Providers

The provider name is validated against the four known providers. Beyond that:

- `replay` is refused with 409: it needs a cassette path, and accepting a filesystem path from a
  browser is exactly the kind of input this API does not take. Replay stays a CLI capability.
- `anthropic` and `openai` are refused with 409 unless the operator set
  `SIGNALFORGE_API_ALLOW_LIVE=1` at startup **and** the credentials and model are configured in the
  server's environment. A request can never supply a key, a model or an endpoint.
- `scripted` is the default and needs nothing.

Provider construction failures are normalised through `ProviderFailure`, so the 409 message names
the missing environment variable and never its value.
