# Synthetic world boundary

**Environment:** SignalForge Demo Commerce. **Dataset label:** Synthetic Operations Environment.
Everything is fictional and generated from `WorldConfig(seed=20260910, reference_start=2026-07-20, reference_end=2026-09-08)`.

## What the world contains (`signalforge.world`)

| Layer | Source | Deterministic under |
|---|---|---|
| Topology: 12 services, 12 infra nodes (5 databases, warehouse, session cache, event bus, search index, DNS, 2 external), 29 edges | `topology.py` (static) | — |
| Baseline deployments (~120), configuration churn (~90), SEV3 alert noise (~40) | `generator.py` with `random.Random(seed)` | seed |
| Authored events for the 15 incidents: deployments, config changes, alerts, metric/log/status effects, the open incident records | `faults.py` | fixed |
| Runbooks (22) and historical incident reviews (12) | `corpus/*.md` with front matter | fixed |
| Metrics | `signals.py`: `value(node, metric, dimension, t)` = baseline profile × diurnal/weekday curve, then active effects (step / ramp / sawtooth), then seeded noise hashed from `(seed, node, metric, dimension, minute)` | seed |
| Logs | `signals.py`: per five-minute bucket, `random.Random(f"{seed}:{node}:{bucket}")` draws baseline lines from templates, then effect lines confined to the effect's active range | seed |
| Health | derived from metrics, open alerts and (for external nodes) status-feed effects at `as_of` | seed |

Nothing is stored on disk. `signalforge world export` writes the server-facing snapshot as JSON for inspection.
Metric and log series are procedural: the same query always yields the same numbers, at any resolution, without a
time-series store. This replaces the `world.sqlite` materialisation proposed in Phase 0; at ~250 discrete records
the in-memory snapshot is simpler and equally reproducible.

## Identity

World record IDs are stable within a world: `DEP-0001…` (chronological), `CFG-0001…`, `ALT-0001…`, `EDGE-<from>-<to>`,
`RB-001…`, `INC-2025-0031` (historical), `INC-2026-0101…0115` (open), `LOG-<node>-<bucket>-<n>`,
`MET-<node>-<metric>[-<dim>]-<start>-<end>-<step>`, `HLT-<node>-<as_of>`.

Authored records receive their IDs from the same chronological numbering as baseline records, so a decoy is not
recognisable by its ID. The generator returns a separate handle→ID **manifest** (`scn01.deploy → DEP-0083`) that only
scenario and evaluation code consume.

## Quiet windows

Each fault declares `quiet_nodes` and a window; baseline deployments, configuration changes and alerts for those
nodes are suppressed inside it. Only authored events (including authored decoys) sit near an incident, which keeps
each scenario's evidence deliberate rather than accidental. Tests assert this.

## Effects are time-bounded

Every effect ends a few hours after its incident so later scenarios start from a clean baseline. The world does
not model remediation (no rollback records after the investigation clock).

## Limits enforced by the server (`ServerLimits`)

72-hour maximum window · 500 log entries · 500 metric points · metric steps {60, 300, 900, 3600} s ·
search limit 10 · `contains` ≤ 120 chars (literal substring, not regex) · query ≤ 200 chars.
