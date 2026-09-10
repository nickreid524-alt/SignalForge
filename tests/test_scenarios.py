"""Scenario catalogue: structure, reproducibility, and satisfiability against the world."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest

from signalforge.retrieval.index import corpus_chunks
from signalforge.scenarios.catalogue import SCENARIOS, scenario_by_id, scenario_for_incident
from signalforge.scenarios.ground_truth import GroundTruth
from signalforge.world.topology import ALL_NODE_IDS

CATEGORIES = {
    "deployment_regression", "db_pool_exhaustion", "memory_leak", "disk_saturation", "queue_backlog",
    "certificate_expiry", "dns_failure", "cache_failure", "dependency_latency", "config_mistake",
    "auth_failure", "traffic_spike", "crash_loop", "third_party_degradation", "inconclusive",
}


def test_catalogue_shape():
    assert len(SCENARIOS) == 15
    assert [s.id for s in SCENARIOS] == [f"SCN-{i:02d}" for i in range(1, 16)]
    assert len({s.incident_id for s in SCENARIOS}) == 15
    assert {s.category for s in SCENARIOS} == CATEGORIES
    assert scenario_by_id("SCN-07").category == "dns_failure"
    assert scenario_for_incident("INC-2026-0114").id == "SCN-14"
    assert scenario_by_id("SCN-99") is None


def test_incidents_exist_in_world(ground_truth: GroundTruth):
    open_ids = {i.id for i in ground_truth.repo.open_incidents()}
    for spec in SCENARIOS:
        assert spec.incident_id in open_ids
        incident = ground_truth.repo.open_incident(spec.incident_id)
        assert incident is not None and incident.affected_service == spec.visible_service


def test_every_scenario_has_a_red_herring_and_an_unacceptable_conclusion():
    for spec in SCENARIOS:
        assert spec.misleading_evidence, spec.id
        assert spec.unacceptable_conclusions, spec.id
        assert spec.expected_useful_tools, spec.id


def test_at_least_five_culprits_outside_the_visible_service():
    outside = [s.id for s in SCENARIOS if s.culprit_outside_visible_service]
    assert len(outside) >= 5, outside


def test_visible_services_are_real_nodes():
    for spec in SCENARIOS:
        assert spec.visible_service in ALL_NODE_IDS


def test_scenarios_do_not_share_one_evidence_pattern():
    shapes = Counter(tuple(sorted({p.kind for p in [*s.decisive_evidence, *s.observable_evidence]})) for s in SCENARIOS)
    assert len(shapes) >= 8, shapes
    kinds = {p.kind for s in SCENARIOS for p in [*s.decisive_evidence, *s.observable_evidence]}
    assert {"record", "metric_change", "log_pattern", "node_status", "document", "dependency_edge", "absence",
            "metric_level", "metric_stable"} <= kinds


def test_handles_resolve_to_world_ids(ground_truth: GroundTruth):
    for spec in SCENARIOS:
        for handle in spec.root_cause_handles:
            assert ground_truth.resolve(handle).split("-")[0] in ("DEP", "CFG", "ALT"), handle
        for decoy in spec.misleading_evidence:
            if decoy.handle:
                assert ground_truth.resolve(decoy.handle)
        for bad in spec.unacceptable_conclusions:
            if bad.handle:
                assert ground_truth.resolve(bad.handle)


@pytest.mark.parametrize("scenario_id", [s.id for s in SCENARIOS])
def test_scenario_evidence_is_satisfiable(ground_truth: GroundTruth, scenario_id: str):
    spec = ground_truth.spec(scenario_id)
    results = ground_truth.evaluate_all(spec)
    failures = [f"{r.predicate.kind}: {r.predicate.description} -> {r.detail}" for r in results if not r.ok]
    assert not failures, "\n".join(failures)
    if spec.category != "inconclusive":
        assert len(spec.decisive_evidence) >= 3, spec.id


def test_inconclusive_scenario_genuinely_lacks_decisive_evidence(ground_truth: GroundTruth):
    spec = ground_truth.spec("SCN-15")
    assert spec.decisive_evidence == []
    assert spec.max_confidence == 0.5
    assert spec.root_cause_handles == []
    repo = ground_truth.repo
    start, end = datetime(2026, 9, 3, 21, 0, tzinfo=UTC), datetime(2026, 9, 4, 9, 20, tzinfo=UTC)
    assert repo.deployments("edge-gateway", start, end) == []
    assert repo.config_changes("edge-gateway", start, end) == []
    # the only signal is a small error-rate change and a low-rate log pattern
    err_before = repo.signals.value("edge-gateway", "error_rate", None, datetime(2026, 9, 4, 2, 0, tzinfo=UTC))
    err_after = repo.signals.value("edge-gateway", "error_rate", None, datetime(2026, 9, 4, 6, 0, tzinfo=UTC))
    assert err_after < 0.01 and err_after > err_before


def test_injection_fixtures_exist_as_chunks(ground_truth: GroundTruth):
    chunk_ids = {c.chunk_id for c in corpus_chunks(ground_truth.repo)}
    fixtures = {f for s in SCENARIOS for f in s.injection_fixtures}
    assert fixtures == {"RB-014#s4", "INC-2026-0044#s4", "RB-020#s3"}
    assert fixtures <= chunk_ids, fixtures - chunk_ids


def test_misleading_records_precede_the_investigation_clock(ground_truth: GroundTruth):
    """A decoy the investigator cannot see is not a decoy."""
    repo = ground_truth.repo
    lookup = {r.id: r for r in [*repo.snapshot.deployments, *repo.snapshot.config_changes, *repo.snapshot.alerts]}
    for spec in SCENARIOS:
        clock = repo.open_incident(spec.incident_id).investigation_clock
        for decoy in spec.misleading_evidence:
            if decoy.handle:
                record = lookup[ground_truth.resolve(decoy.handle)]
                when = getattr(record, "started_at", None) or getattr(record, "applied_at", None) or record.fired_at
                assert when <= clock, (spec.id, decoy.handle, when, clock)
