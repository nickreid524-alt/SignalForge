# Audit trace

`signalforge.audit.TraceStore` persists every investigation to SQLite
(`runs/signalforge.sqlite` by default; `:memory:` in tests). Schema version 1 is
recorded in `schema_version`; opening a database with another version fails.

| Table | Rows |
|---|---|
| `investigations` | id, incident, provider name/model/mode/`uses_llm`, transport, start/end, status, termination reason, budget, usage, incident brief, error |
| `status_changes` | every state-machine transition with timestamp and note |
| `steps` | per deliberation step: model call id, assistant text, finish flag |
| `model_calls` | id, step, purpose (`deliberate` / `report` / `repair`), request fingerprint, tool count, latency, tokens, stop reason, response (redacted), error |
| `actions` | every requested action: kind, name, arguments, accepted or rejection code/reason, evidence id, duplicate-of, ok, error, latency |
| `evidence` | every registered item: sequence, source, arguments, ok, result kind, source ids, content hash, full payload/text (redacted), latency |
| `hypothesis_updates` | each applied update with statement, confidence, status, evidence ids |
| `validations` | each round: ok, error/warning counts, issues, the draft as submitted, parse error |
| `repairs` | the repair request text sent back to the provider per round |
| `reports` | the final `InvestigationReport` (valid or marked invalid) |

## Guarantees

- **No secrets.** All free text passes through `redact()` (API-key shapes, bearer tokens,
  `key=value` secrets). Provider configuration is stored as names and modes only; API keys never
  reach the store because providers never expose them.
- **Failed and rejected work is recorded too**: failed tool calls appear as evidence with `ok=0`;
  suppressed duplicates and rejected actions appear in `actions` with their reason.
- **Answering "what did the system inspect before concluding?"** `export_investigation()` produces
  a JSON bundle with every table plus an `inspection_summary`: evidence gathered, evidence cited in
  the report, evidence gathered but uncited, tool calls made, and suppressed/rejected actions.

## CLI

```
signalforge investigate INC-2026-0106 --json runs/inc-0106.json   # runs and exports
signalforge trace <investigation-id>                                # human-readable walk of the trace
signalforge trace --list
```
