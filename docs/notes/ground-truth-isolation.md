# Ground-truth isolation

The MCP server must not be able to answer "what is the root cause of INC-2026-0107?". Ground truth
(root cause, decisive-evidence rubric, red-herring labels, expected tools, unacceptable conclusions,
injection-fixture list) lives exclusively in `signalforge.scenarios`.

## Mechanism

1. **Package boundary.** `signalforge.world`, `signalforge.mcp_server`, `signalforge.retrieval`,
   `signalforge.mcp_client` and `signalforge.evidence` never import `signalforge.scenarios`.
   `tests/test_isolation.py::test_server_facing_code_never_imports_scenarios` parses every module with `ast`
   and fails on any such import.
2. **Data boundary.** `generate_world()` returns `GeneratedWorld(snapshot, manifest)`. The server is built from
   `snapshot` only (`create_server(snapshot=...)` / `build_snapshot()`); `WorldSnapshot` has no manifest field and
   `WorldRepository` has no accessor for it. Fault definitions carry handles, but handles are stripped when
   records are assigned world IDs.
3. **Schema boundary.** No server-facing model has a field whose name contains `root_cause`, `decisive`,
   `red_herring`, `misleading`, `expected_useful`, `unacceptable`, `ground_truth`, `culprit`, `manifest` or
   `handle`. Open incidents carry title, description, times, severity, service, reporter — nothing causal.
4. **Content check.** The test serialises every non-corpus record the server can return (incidents, deployments,
   config changes, alerts, effects) and asserts that no scenario ID, root-cause statement, handle, or the words
   "root cause", "red herring", "decoy", "ground truth", "decisive" appear.

Historical incident reviews in the corpus do contain root-cause sections — they are *past* incidents, and
retrieving them is the point of `search_incidents`. Several are deliberate near-misses for live scenarios.

## What the scenarios package holds

`ScenarioSpec` (15 instances in `catalogue.py`): category, difficulty, visible service vs culprit location,
root-cause statement, root-cause handles/terms, decisive and observable evidence predicates, misleading
evidence, expected useful tools, unacceptable conclusions, injection fixtures, and `max_confidence` for the
inconclusive case. `GroundTruth` (`ground_truth.py`) owns both the world and the manifest and evaluates
predicates against the repository and retriever — the building block for the Phase 3 evaluation harness.
`tests/test_scenarios.py` proves every predicate is satisfiable in the generated world, that seven scenarios
place the culprit outside the visibly failing service, and that SCN-15 genuinely has no decisive evidence.
