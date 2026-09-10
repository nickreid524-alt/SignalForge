# Grounding contract

The canonical result of an investigation is a typed `InvestigationReport`
(`signalforge.reports.schema`). Markdown is a rendering of it. A report is only
accepted when it passes `GroundingValidator` against the investigation's own
evidence registry.

## Claim kinds

| Kind | Meaning | Citation rule |
|---|---|---|
| `OBSERVED` | directly present in a tool result or resource | ≥1 citation, and at least one cited item must be a *successful* tool/resource result |
| `INFERRED` | reasoning over observations | ≥1 citation |
| `UNKNOWN` | not established | no citations |

Citations use the Phase 1 grammar: `EVD-000004` or `EVD-000004#DEP-0038`.

## Rules

| Rule | Severity | Detects |
|---|---|---|
| G0 | error | draft did not parse against the schema |
| G1 | error | malformed citation; citation to evidence not gathered in this investigation (covers foreign investigations, since ids are registry-scoped) |
| G2 | error | narrowed citation to a record not inside that evidence item |
| G3 | error | OBSERVED/INFERRED claim with no evidence; UNKNOWN claim with evidence; OBSERVED claim supported only by failed calls; non-UNKNOWN entries under `unknowns` |
| G4 | error | `root_cause_identified`/`probable_cause` without a primary hypothesis or without supporting evidence; `root_cause_identified` supported by fewer than two kinds of evidence |
| G5 | error | `root_cause_identified` with confidence < 0.6; `inconclusive` with confidence > 0.5, no unknowns, or a "supported" primary |
| G6 | error / warning | refuted primary; primary not among `hypotheses_considered` (error); report confidence differing from the primary by > 0.1 (warning) |
| G7 | error | mitigation / follow-up action without evidence (diagnostics are exempt) |
| G8 | warning | a failed call cited as support |
| G9 | info | gathered evidence never cited |
| G11 | error | report and registry belong to different investigations |

Errors block acceptance; warnings yield `completed_with_warnings`; info never changes the outcome.

## Repair loop

If validation fails, the orchestrator sends the exact violations back to the provider
(`render_validation_failure`) and requests a new draft, up to `max_repair_rounds` times. Every round is
stored: the original draft, its issues, the repair request text and the repaired draft (`validations`
and `repairs` tables). If errors remain, the investigation ends `failed_validation` and the last draft
is kept in the report **marked invalid**; it is never presented as an accepted conclusion.

## What is not claimed

The validator proves structure, provenance and coherence. It does not prove that a cited item
semantically supports its sentence. That gap is measured by the evaluation harness
(decisive-citation recall, red-herring adoption, calibration) rather than asserted.
