# Evidence identity

Two identifier layers, two owners.

| Layer | Owner | Example | Scope |
|---|---|---|---|
| World record ID | MCP server | `DEP-0083`, `CFG-0061`, `LOG-checkout-20260803T0810-004`, `MET-checkout-latency_p95-20260803T0544-20260803T0844-300`, `RB-016#s3` | global within a generated world |
| Evidence ID | client-side `EvidenceRegistry` | `EVD-000004` | one investigation |

Every evidence-producing tool result is an `EvidenceEnvelope` carrying `kind`, `source_ids` (the world IDs it
contains, in order), the validated `query`, time bounds, `truncated`, a deterministic `summary` and the
`data_notice`. Resource reads carry their identity in the URI (`runbook://RB-016`) or in a `source_ids` array
inside JSON resources. The server never mints investigation-local IDs.

## Registry entry (`EvidenceItem`)

`evidence_id`, `investigation_id`, `sequence`, `acquired_at`, `source_kind` (tool | resource), `source_name`,
`arguments`, `ok`, `error`, `result_kind`, `source_ids`, `content_hash` (`sha256:` over canonical JSON of payload
and text), `payload`, `text`, `latency_ms`. Failed calls are registered too (so a trace can show what was tried)
but carry no `source_ids` and cannot support a claim.

## Citation grammar

```
EVD-000004              whole item
EVD-000004#DEP-0083     one record inside the item
```

`CitationValidator.check(text)` returns a code:

| Code | Meaning |
|---|---|
| `ok` | item exists in this investigation; record (if given) is inside it |
| `malformed` | not citation-shaped |
| `unknown_evidence` | never gathered in this investigation |
| `unknown_record` | narrowed record not among the item's `source_ids` |
| `error_evidence` | item is a failed call (accepted with a warning; cannot support a claim) |
| `foreign_investigation` | an `EvidenceItem` object from another investigation (`check_item`) |

Because EVD numbering is per investigation, a citation string is always resolved against one registry;
`check_item` additionally compares `investigation_id` and `content_hash` so an item cannot be smuggled across
investigations. `extract_citations(text)` pulls citation tokens out of free text for the Phase 2 report validator.
