# Deterministic demonstration provider

`ScriptedDemoProvider` (`signalforge.providers.scripted`) implements the neutral
`ModelProvider` boundary without any language model. **It is not an LLM and does
not pretend to be one**: its `ProviderInfo` says `mode="scripted"`,
`uses_llm=False`, and the CLI prints `SCRIPTED DEMONSTRATION MODE - No LLM API is
being used` whenever it runs.

## Why it exists

To exercise, test and demonstrate the whole pipeline without paid credentials:

```
playbook -> ScriptedDemoProvider -> Investigator -> ActionPolicy -> OpsClient -> MCP server
        -> EvidenceRegistry -> GroundingValidator -> TraceStore -> evaluation harness
```

Everything after the provider is the production code path. The provider never reads the synthetic
world directly; it only sees what the orchestrator sends back over the boundary, exactly as a live
model would.

## Playbooks (`signalforge.providers.playbooks`)

One `Playbook` per open incident: ordered steps of tool calls and resource reads, hypothesis updates,
and a report template. Playbooks speak in **labels**, never in runtime ids:

- `call("deploys", "get_deployments", service="@service", start="@clock-6h", end="@clock")` — times are
  relative to the incident's investigation clock; `@service` is the affected service.
- `read("runbook", "@hit:rb_search:0")` — read the first resource URI returned by the labelled search.
- Evidence references in hypotheses and the report: `@deploys` (the whole result) or `@deploys#0`
  (narrowed to the first record id of that result, e.g. `EVD-000004#DEP-0038`).

At run time the provider parses the evidence headers the orchestrator renders (`evidence_id`,
`source_ids`, `resource_uris`) and the `update_hypotheses` results (`{"assigned": {"h_deploy": "H1"}}`) to
resolve labels to real `EVD` and `H` ids. Unresolvable labels are dropped, which makes the report worse
and is visible in evaluation - nothing is fabricated to compensate.

Playbooks were authored with knowledge of the scenarios, the way a human writes a runbook for an incident
they have seen. They are static data; `tests/test_firewall_and_injection.py` proves that no ground-truth
module is imported while one runs.

## What a scripted run does and does not show

Shows: real MCP calls with validated arguments, evidence registration and hashing, policy enforcement,
hypothesis evolution, grounding validation, trace persistence, evaluation scoring, injection fixtures
flowing through as inert data.

Does not show: reasoning. The scripted provider cannot recover from unexpected evidence, cannot repair a
report (it re-emits the same draft), and cannot investigate an incident without a playbook.

## Replay provider

`RecordingProvider` wraps any provider and records every turn keyed by a request fingerprint;
`ReplayProvider` serves the recording back. Strict mode fails with `ReplayMismatch` when the
conversation diverges from the recording. Cassettes are how live-model behaviour will be captured and
tested offline in later phases.
