"""Synthetic world: determinism, referential integrity, procedural signals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from signalforge.config import WorldConfig
from signalforge.world.faults import FAULTS
from signalforge.world.generator import generate_world
from signalforge.world.repository import WorldRepository
from signalforge.world.signals import MetricQueryError
from signalforge.world.topology import ALL_NODE_IDS, SERVICE_IDS

T0 = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)


def test_generation_is_deterministic(generated):
    again = generate_world(WorldConfig())
    assert again.snapshot.model_dump_json() == generated.snapshot.model_dump_json()
    assert dict(again.manifest) == dict(generated.manifest)


def test_different_seed_changes_baseline_but_not_authored_records(generated):
    other = generate_world(WorldConfig(seed=7))
    assert other.snapshot.model_dump_json() != generated.snapshot.model_dump_json()
    # authored incidents are seed-independent
    assert [i.id for i in other.snapshot.open_incidents] == [i.id for i in generated.snapshot.open_incidents]
    # every handle still resolves
    assert set(other.manifest) == set(generated.manifest)


def test_world_shape(generated):
    s = generated.snapshot
    assert len(s.services) == 12
    assert len(s.nodes) == 12
    assert len(s.open_incidents) == 15
    assert len(s.runbooks) == 22
    assert len(s.historical_incidents) == 12
    assert len(s.deployments) > 100
    assert len(s.config_changes) > 50
    assert len(s.alerts) > 30
    assert s.environment == "SignalForge Demo Commerce"
    assert s.dataset_label == "Synthetic Operations Environment"


def test_dependency_edges_reference_known_nodes(generated):
    s = generated.snapshot
    for edge in s.edges:
        assert edge.source in SERVICE_IDS, edge
        assert edge.target in ALL_NODE_IDS, edge
        assert edge.id == f"EDGE-{edge.source}-{edge.target}"
    assert len({e.id for e in s.edges}) == len(s.edges)
    for svc in s.services:
        for ds in svc.datastores:
            assert ds in ALL_NODE_IDS, (svc.id, ds)


def test_change_records_are_valid_and_sequential(generated):
    s = generated.snapshot
    dep_ids = [d.id for d in s.deployments]
    assert dep_ids == [f"DEP-{i:04d}" for i in range(1, len(dep_ids) + 1)]
    assert [d.started_at for d in s.deployments] == sorted(d.started_at for d in s.deployments)
    for d in s.deployments:
        assert d.service in SERVICE_IDS
        assert d.version.startswith("v2026.")
        assert d.finished_at > d.started_at
        assert d.change_notes
    cfg_ids = [c.id for c in s.config_changes]
    assert cfg_ids == [f"CFG-{i:04d}" for i in range(1, len(cfg_ids) + 1)]
    assert all(c.node in ALL_NODE_IDS for c in s.config_changes)
    alt_ids = [a.id for a in s.alerts]
    assert alt_ids == [f"ALT-{i:04d}" for i in range(1, len(alt_ids) + 1)]
    assert all(a.node in ALL_NODE_IDS for a in s.alerts)
    assert all(a.resolved_at is None or a.resolved_at >= a.fired_at for a in s.alerts)


def test_versions_unique_per_service(generated):
    seen = set()
    for d in generated.snapshot.deployments:
        assert (d.service, d.version) not in seen, (d.service, d.version)
        seen.add((d.service, d.version))


def test_manifest_covers_every_authored_record(generated):
    handles = {inj.handle for f in FAULTS for inj in f.injected}
    assert set(generated.manifest) == handles
    ids = set(generated.manifest.values())
    all_ids = {r.id for r in [*generated.snapshot.deployments, *generated.snapshot.config_changes,
                              *generated.snapshot.alerts]}
    assert ids <= all_ids


def test_effects_reference_known_nodes(generated):
    for eff in [*generated.snapshot.metric_effects, *generated.snapshot.log_effects,
                *generated.snapshot.status_effects]:
        assert eff.node in ALL_NODE_IDS, eff


def test_quiet_windows_suppress_baseline_noise(generated):
    """Only authored records for involved nodes fall inside a fault's quiet window."""
    s = generated.snapshot
    authored = set(generated.manifest.values())
    for fault in FAULTS:
        for d in s.deployments:
            if d.service in fault.quiet_nodes and fault.quiet_start <= d.started_at <= fault.quiet_end:
                assert d.id in authored, (fault.id, d)
        for c in s.config_changes:
            if c.node in fault.quiet_nodes and fault.quiet_start <= c.applied_at <= fault.quiet_end:
                assert c.id in authored, (fault.id, c)


def test_open_incidents_carry_no_cause_fields(generated):
    from signalforge.world.models import OpenIncident

    names = set(OpenIncident.model_fields)
    assert not any(n for n in names if "cause" in n or "culprit" in n or "expected" in n)
    for inc in generated.snapshot.open_incidents:
        assert inc.investigation_clock == inc.detected_at + timedelta(minutes=20)
        assert inc.affected_service in SERVICE_IDS


def test_metric_series_is_stable_and_bounded(repo: WorldRepository):
    a = repo.metric_series("checkout", "latency_p95", None, T0, T0 + timedelta(hours=3), 300)
    b = repo.metric_series("checkout", "latency_p95", None, T0, T0 + timedelta(hours=3), 300)
    assert [p.value for p in a] == [p.value for p in b]
    assert len(a) == 36
    assert all(p.value > 0 for p in a)
    # the SCN-01 step is visible: p95 goes from ~190 ms to well above 2 s after 08:11
    before = [p.value for p in a if p.timestamp < datetime(2026, 8, 3, 8, 11, tzinfo=UTC)]
    after = [p.value for p in a if p.timestamp >= datetime(2026, 8, 3, 8, 15, tzinfo=UTC)]
    assert max(before) < 400
    assert min(after) > 2000


def test_metric_noise_is_bounded_and_seeded(repo: WorldRepository):
    values = [p.value for p in repo.metric_series("pricing", "request_rate", None, T0, T0 + timedelta(hours=1), 60)]
    assert min(values) > 200 and max(values) < 400
    assert len(set(values)) > 5  # not constant


def test_log_generation_is_stable_and_filtered(repo: WorldRepository):
    start, end = datetime(2026, 8, 3, 8, 0, tzinfo=UTC), datetime(2026, 8, 3, 8, 30, tzinfo=UTC)
    a = repo.logs("checkout", start, end)
    b = repo.logs("checkout", start, end)
    assert [e.id for e in a] == [e.id for e in b]
    assert all(start <= e.timestamp < end for e in a)
    assert [e.timestamp for e in a] == sorted(e.timestamp for e in a)
    assert len({e.id for e in a}) == len(a)
    assert all(e.id.startswith("LOG-checkout-") for e in a)
    fanout = [e for e in a if e.pattern_id == "checkout.reserve_fanout"]
    assert fanout and all(e.timestamp >= datetime(2026, 8, 3, 8, 11, tzinfo=UTC) for e in fanout)


def test_metric_validation_errors_are_informative(repo: WorldRepository):
    with pytest.raises(MetricQueryError, match="Unknown node"):
        repo.signals.available_metrics("nope")
    with pytest.raises(MetricQueryError, match="requires a dimension"):
        repo.signals.value("checkout", "dependency_latency_p95", None, T0)
    with pytest.raises(MetricQueryError, match="Unknown dimension"):
        repo.signals.value("checkout", "dependency_latency_p95", "dependency=mailrelay", T0)
    with pytest.raises(MetricQueryError, match="external dependency"):
        repo.signals.value("payvault", "error_rate", None, T0)
    with pytest.raises(MetricQueryError, match="not available"):
        repo.signals.value("dns", "queue_lag", None, T0)


def test_health_snapshots(repo: WorldRepository):
    healthy = repo.health("checkout", datetime(2026, 8, 3, 7, 30, tzinfo=UTC))
    assert healthy.status == "healthy"
    sick = repo.health("checkout", datetime(2026, 8, 3, 8, 44, tzinfo=UTC))
    assert sick.status in ("degraded", "critical")
    assert sick.open_alert_ids
    payvault = repo.health("payvault", datetime(2026, 9, 2, 12, 18, tzinfo=UTC))
    assert payvault.status == "critical" and payvault.status_feed == "major_outage"
    fine = repo.health("payvault", datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    assert fine.status == "healthy" and fine.status_feed == "operational"


def test_window_clamp(repo: WorldRepository):
    with pytest.raises(MetricQueryError, match="too large"):
        repo.window_clamped(T0, T0 + timedelta(hours=73))
    with pytest.raises(MetricQueryError, match="after start"):
        repo.window_clamped(T0, T0)
    assert repo.window_clamped(T0, T0 + timedelta(hours=72))
