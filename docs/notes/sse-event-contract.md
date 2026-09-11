# The SSE investigation event contract

`GET /api/investigations/{id}/events` streams `text/event-stream`. This is the contract a frontend
may depend on. It is deliberately **not** the audit trace.

## Two different records

| | audit trace (`signalforge.audit`) | event stream (`signalforge.events`) |
|---|---|---|
| Audience | engineers, evaluation, debugging | user interfaces |
| Content | deep internal history, request fingerprints, drafts, raw rows | stable application facts |
| Schema | SQLite tables, free to change | typed payload models, versioned as a contract |
| Access | `GET .../trace`, a curated projection | this stream |

Keeping them apart means the trace schema can evolve without breaking a frontend, and the stream can
stay small and meaningful without losing forensic depth. A test asserts that no SQLite column name
(`*_json`, `request_fingerprint`, …) ever appears in an event payload.

## Frame format

```
id: 17
event: tool.completed
data: {"seq": 17, "investigation_id": "inv-inc-2026-0114-ab12cd34", "type": "tool.completed",
       "at": "2026-09-11T10:31:02.184000+00:00", "payload": {...}}
```

- `id` is the **per-investigation sequence number**: monotonic, gap-free, starting at 1. It is also
  the reconnection cursor.
- `event` is the type, so a browser can use `addEventListener(type, ...)`.
- `data` repeats the sequence, id, type and timestamp alongside the typed payload.

## Event types

| Type | Payload highlights |
|---|---|
| `investigation.created` | incident, provider, provider mode, `uses_live_api`, model, budget profile |
| `status.changed` | `from_status`, `to_status`, note (the state machine, verbatim) |
| `step.started` | step number, budget remaining |
| `provider.completed` | step, purpose, model, stop reason, latency, tool-request count, token usage |
| `tool.requested` | step, request id, tool name, arguments |
| `tool.completed` | evidence id, ok, error, latency |
| `tool.rejected` | name, policy code, reason, duplicate-of — the prompt-injection signal |
| `resource.read` | uri, evidence id, ok |
| `evidence.registered` | evidence id, source, record count, `untrusted: true` |
| `hypothesis.updated` | id, statement, status, confidence, supporting and contradicting ids |
| `validation.started` / `validation.failed` | round, error and warning counts, failing rule ids |
| `repair.started` | round, reason |
| `report.completed` | status, confidence, primary hypothesis id, validation outcome |
| `investigation.completed` | terminal status, counters, duration, whether a report exists |
| `investigation.failed` | terminal status, message, normalised error category |

Each type has exactly one payload model in `events/models.py`, and `InvestigationEvent.build`
refuses a payload of the wrong type. Payload models set `extra="forbid"`, so an accidental leak
fails loudly rather than reaching a browser. A test validates every event of a real run against its
declared model.

**Terminal events.** Exactly one arrives, and the stream closes after it. `investigation.completed`
covers every run that finished the pipeline, with `terminal_status` distinguishing `completed`,
`completed_with_warnings` and `failed_validation`; a report that failed grounding validation is
still a completed investigation with a report to show. `investigation.failed` is for a run the
engine could not finish (provider failure, transport, bug) and carries the normalised category.

**Hidden reasoning never appears.** Provider opaque blocks are held in memory for same-vendor
continuity only. They are absent from the trace, the cassette and these events;
`provider.completed` carries a short preview of the *visible* assistant text and nothing else.

## Reconnection and replay

The durable source is SQLite (`events` table), not an in-process queue. That is what makes replay
honest: a browser that was disconnected for a minute gets the minute it missed.

The handler ordering matters and is the whole design:

1. **subscribe first**, so nothing appended during step 2 is lost;
2. read the backlog after the cursor from SQLite;
3. switch to the live queue, discarding by sequence anything already sent;
4. stop at the terminal event.

The cursor comes from the standard `Last-Event-ID` header, which browsers send automatically on
reconnect, or from an explicit `?after=N` for clients that prefer it. A cursor that is not an
integer in range is a 400 with the usual error envelope.

Connecting to an investigation that already finished replays from the cursor and closes, rather
than hanging: the handler checks for a stored terminal event after draining the backlog.

A subscriber that cannot keep up is dropped rather than allowed to stall an investigation; the
browser reconnects with its last id and is served the gap from SQLite.

Tested in `tests/test_api_events.py`: ordering, gap-free unique ids, payload conformance, every
cursor position from 0 to N, header and query equivalence, connecting after completion,
disconnecting mid-flight and resuming with no gap and no duplicate, invalid cursors, the failure
event, and absence of hidden reasoning. `tests/test_api_http_integration.py` repeats the core of it
over a real socket.
