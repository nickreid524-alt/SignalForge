"""Ground-truth access and predicate evaluation against the generated world.

This module is the *only* place that holds both the world and the fault
manifest. It is used by tests now and by the evaluation harness later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from signalforge.config import WorldConfig
from signalforge.retrieval import LexicalRetriever, build_retriever
from signalforge.scenarios.catalogue import SCENARIOS, scenario_by_id, scenario_for_incident
from signalforge.scenarios.models import EvidencePredicate, ScenarioSpec
from signalforge.world.generator import generate_world
from signalforge.world.repository import WorldRepository
from signalforge.world.signals import MetricQueryError


@dataclass(frozen=True)
class PredicateResult:
    predicate: EvidencePredicate
    ok: bool
    detail: str


class GroundTruth:
    def __init__(self, config: WorldConfig | None = None) -> None:
        generated = generate_world(config)
        self.repo = WorldRepository(generated.snapshot)
        self.manifest = generated.manifest
        self.retriever: LexicalRetriever = build_retriever(self.repo)

    # ------------------------------------------------------------------ specs
    def specs(self) -> tuple[ScenarioSpec, ...]:
        return SCENARIOS

    def spec(self, scenario_id: str) -> ScenarioSpec:
        found = scenario_by_id(scenario_id)
        if found is None:
            raise KeyError(scenario_id)
        return found

    def spec_for_incident(self, incident_id: str) -> ScenarioSpec:
        found = scenario_for_incident(incident_id)
        if found is None:
            raise KeyError(incident_id)
        return found

    def resolve(self, handle: str) -> str:
        """Map an authored-record handle (``scn01.deploy``) to its world ID (``DEP-0083``)."""
        return self.manifest[handle]

    def root_cause_record_ids(self, spec: ScenarioSpec) -> list[str]:
        return [self.resolve(h) for h in spec.root_cause_handles]

    # ------------------------------------------------------------------ predicates
    def evaluate(self, pred: EvidencePredicate) -> PredicateResult:
        try:
            ok, detail = self._evaluate(pred)
        except MetricQueryError as exc:
            ok, detail = False, f"query error: {exc}"
        return PredicateResult(pred, ok, detail)

    def evaluate_all(self, spec: ScenarioSpec) -> list[PredicateResult]:
        return [self.evaluate(p) for p in [*spec.decisive_evidence, *spec.observable_evidence]]

    def _record(self, record_id: str) -> bool:
        s = self.repo.snapshot
        return any(r.id == record_id for r in [*s.deployments, *s.config_changes, *s.alerts])

    def _evaluate(self, p: EvidencePredicate) -> tuple[bool, str]:
        repo = self.repo
        if p.kind == "record":
            assert p.handle
            record_id = self.manifest.get(p.handle)
            if record_id is None:
                return False, f"handle {p.handle} not in manifest"
            return self._record(record_id), f"{p.handle} -> {record_id}"

        if p.kind in ("metric_change", "metric_stable"):
            assert p.node and p.metric and p.at
            before = repo.signals.value(p.node, p.metric, p.dimension, p.at - timedelta(minutes=30))
            after = repo.signals.value(p.node, p.metric, p.dimension, p.at + timedelta(minutes=20))
            if before == 0 and after == 0:
                ratio = 1.0
            elif before == 0 or after == 0:
                ratio = float("inf")
            else:
                ratio = max(after / before, before / after)
            detail = f"{p.node}.{p.metric}[{p.dimension}] before={before} after={after} ratio={ratio:.2f}"
            if p.kind == "metric_change":
                assert p.min_change_ratio is not None
                return ratio >= p.min_change_ratio, detail
            assert p.max_change_ratio is not None
            return ratio <= p.max_change_ratio, detail

        if p.kind == "metric_level":
            assert p.node and p.metric and p.at
            value = repo.signals.value(p.node, p.metric, p.dimension, p.at)
            ok = (p.min_value is None or value >= p.min_value) and (p.max_value is None or value <= p.max_value)
            return ok, f"{p.node}.{p.metric} at {p.at.isoformat()} = {value}"

        if p.kind == "log_pattern":
            assert p.node and p.pattern_id and p.window_start and p.window_end
            entries = repo.logs(p.node, p.window_start, p.window_end)
            count = sum(1 for e in entries if e.pattern_id == p.pattern_id)
            return count > 0, f"{p.pattern_id} x{count} in {len(entries)} lines"

        if p.kind == "node_status":
            assert p.node and p.at and p.status
            health = repo.health(p.node, p.at)
            return health.status == p.status, f"{p.node} status={health.status} ({health.detail})"

        if p.kind == "document":
            assert p.document_id and p.query
            kind = "runbook" if p.document_id.startswith("RB-") else "incident"
            hits = self.retriever.search(p.query, source_kind=kind, limit=p.top_k)  # type: ignore[arg-type]
            found = [h.source_id for h in hits]
            return p.document_id in found, f"top-{p.top_k} for {p.query!r}: {found}"

        if p.kind == "dependency_edge":
            assert p.edge_id
            return any(e.id == p.edge_id for e in repo.snapshot.edges), p.edge_id

        if p.kind == "absence":
            assert p.node and p.record_kind and p.window_start and p.window_end
            if p.record_kind == "deployments":
                rows = repo.deployments(p.node, p.window_start, p.window_end)
            elif p.record_kind == "config_changes":
                rows = repo.config_changes(p.node, p.window_start, p.window_end)
            else:
                rows = repo.alerts(p.node, p.window_start, p.window_end)
            return len(rows) == 0, f"{len(rows)} {p.record_kind} for {p.node} in window"

        return False, f"unknown predicate kind {p.kind}"
