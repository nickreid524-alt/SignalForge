# Replay cassette format (version 2)

A cassette is a JSON file that records every provider interaction of one or more investigations
in **neutral** terms so that `ReplayProvider` can stand in for any provider - scripted or live -
without a model, a key or a network. Phase 3 bumps the format to version 2 to carry usage,
latency, provider identity and the full neutral request, and to make the sanitisation explicit.

## Recording

```python
from signalforge.providers.replay import RecordingProvider
recorder = RecordingProvider(inner_provider)         # wraps any ModelProvider
... run investigations with recorder ...
path = recorder.cassette.save("runs/my-run.cassette.json")
```

`RecordingProvider` forwards every `complete` / `generate_structured` call to the inner provider
and appends one `CassetteEntry` per call. Before an entry is stored it is **sanitised**:

- opaque provider blocks (thinking blocks, reasoning items) are stripped from both the request
  conversation and the recorded turn (`strip_opaque`);
- every string is passed through `signalforge.redaction.redact` (API keys, bearer tokens,
  `api_key=` pairs, GitHub tokens).

Raw HTTP requests or responses, headers, SDK objects and vendor-native payloads are never in a
cassette; only the neutral objects from `providers/base.py` are.

## Structure

```json
{
  "version": 2,
  "provider": { "name": "anthropic", "model": "...", "mode": "live", "uses_llm": true, ... },
  "model": "...",
  "created_at": "2026-09-10T12:00:00Z",
  "sanitized": true,
  "entries": [
    {
      "index": 0,
      "purpose": "deliberate",
      "fingerprint": "sha256 of the neutral request (opaque excluded)",
      "kind": "turn",
      "request": {
        "system": "...",
        "conversation": [ {"role": "user", "text": "..."}, {"role": "assistant", "text": "...", "tool_requests": [...], "opaque": []}, {"role": "tool_results", "results": [...]} ],
        "tool_names": ["get_service_health", "..."],
        "tools_digest": "sha256 of the tool specs",
        "schema_name": null,
        "context": { "investigation_id": "...", "incident_id": "...", "step": 1, "purpose": "deliberate", ... }
      },
      "turn": { "text": "...", "tool_requests": [...], "opaque": [], "usage": {...}, "latency_ms": 812.4, "stop_reason": "tool_use" },
      "usage": { "reported": true, "input_tokens": 1520, "output_tokens": 88, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_output_tokens": 0 },
      "latency_ms": 812.4
    },
    {
      "index": 3,
      "purpose": "report",
      "kind": "structured",
      "schema_name": "ReportDraft",
      "structured_raw": { "...the JSON the model returned..." },
      "parse_error": null,
      "request": { "...": "..." }, "usage": { "...": "..." }, "latency_ms": 2210.0
    }
  ]
}
```

Field notes:

- `provider` is the `ProviderInfo` of the recorded provider, so a replay knows and prints what
  it is replaying (`REPLAY MODE`, `USES LIVE API: NO`, plus the original provider name/model).
- `kind` is `turn` (from `complete`) or `structured` (from `generate_structured`).
- `usage` is copied from the recorded provider. Cassettes of scripted runs therefore carry
  `reported: false`; cassettes of live runs carry the real counts, and a replay reproduces the
  original token telemetry in the report and the trace.
- `structured_raw` holds the JSON the model produced; `ReplayProvider` re-validates it against
  the requested schema, so a replay exercises the same parsing and repair path as the original.

## Replay

```python
from signalforge.providers.replay import Cassette, ReplayProvider
provider = ReplayProvider.from_file("runs/my-run.cassette.json")      # strict by default
```

`ReplayProvider` walks the entries in order. For each call it checks that the next entry has the
requested `kind` and the same `fingerprint` as the live request; a mismatch raises
`ReplayMismatch` naming the index and both fingerprints. `Cassette.load` rejects any version other
than 2 with a clear message rather than guessing. Because fingerprints exclude opaque blocks and
include the tool digest, a recording is replayable as long as the prompts, tool schemas and the
world behind MCP are unchanged - which is exactly the regression signal wanted.

## Tests

`tests/test_replay_and_trace.py` (Phase 2 behaviour), `tests/test_provider_contract.py::test_cassette_v2_contract`
(records a live-like Anthropic run over a fake vendor, checks the on-disk shape, the absence of
thinking signatures / key material / encrypted reasoning, replays it and compares reports and
token usage), and the contract parity test that replays a scripted recording.
