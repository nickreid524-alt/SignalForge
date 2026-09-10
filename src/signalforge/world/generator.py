"""Deterministic world generation.

``generate_world(config)`` returns the server-facing :class:`WorldSnapshot`
*and* a separate handle→ID manifest for records authored by fault injections.
The manifest is evaluation plumbing (it lets scenario specs say "the deploy
we authored as scn01.deploy") and is never attached to the snapshot.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

from signalforge.config import DATASET_LABEL, ENVIRONMENT_NAME, WorldConfig
from signalforge.world import corpus
from signalforge.world.faults import FAULTS, PENDING_ID, FaultInjection
from signalforge.world.models import (
    Alert,
    ConfigChange,
    Deployment,
    WorldSnapshot,
)
from signalforge.world.topology import EDGES, NODES, SERVICES

BASELINE_CHANGE_NOTES = [
    "Dependency updates", "Refactor request logging", "Add tracing spans for outbound calls",
    "Fix flaky retry test", "Improve error messages for validation failures", "Tune HTTP client pool",
    "Update dashboard annotations", "Remove deprecated endpoint /v0/status", "Bump base image",
    "Extend metrics with per-route histograms",
]

BASELINE_CONFIG_KEYS: list[tuple[str, str, str, str]] = [
    ("log_level", "INFO", "DEBUG", "Investigating intermittent warnings"),
    ("log_level", "DEBUG", "INFO", "Revert verbose logging"),
    ("http.client_timeout_ms", "2000", "2500", "Reduce spurious timeouts"),
    ("feature_flags.new_dashboard_widgets", "false", "true", "Gradual rollout"),
    ("cache.ttl_seconds", "300", "600", "Reduce origin load"),
    ("autoscaler.target_cpu", "0.65", "0.70", "Capacity tuning"),
    ("tracing.sample_rate", "0.05", "0.10", "Better trace coverage"),
]

BASELINE_ALERT_RULES: list[tuple[str, str]] = [
    ("cpu-high-warning", "cpu utilisation above 70% for 10m"),
    ("latency-slo-warning", "p95 latency above warning threshold for 5m"),
    ("disk-usage-warning", "disk usage above 75%"),
    ("pod-restart", "one pod restarted (node drain)"),
    ("certificate-expiry-30d", "TLS certificate expires in 30 days"),
]

TEAM_OF_NODE: dict[str, str] = {s.id: s.team for s in SERVICES}


@dataclass(frozen=True)
class GeneratedWorld:
    snapshot: WorldSnapshot
    manifest: Mapping[str, str]  # fault handle -> assigned world record id


def _quiet(node: str, at: datetime, faults: tuple[FaultInjection, ...]) -> bool:
    return any(node in f.quiet_nodes and f.quiet_start <= at <= f.quiet_end for f in faults)


def _baseline_deployments(rng: random.Random, config: WorldConfig, faults: tuple[FaultInjection, ...]) -> list[Deployment]:
    deployments: list[Deployment] = []
    for service in SERVICES:
        cursor = config.reference_start + timedelta(days=rng.uniform(0.5, 3.5))
        while cursor < config.reference_end:
            started = cursor.replace(hour=rng.randint(9, 16), minute=rng.choice([0, 5, 10, 20, 30, 45]))
            if not _quiet(service.id, started, faults):
                notes = rng.sample(BASELINE_CHANGE_NOTES, k=rng.randint(1, 2))
                deployments.append(Deployment(
                    id=PENDING_ID, service=service.id, version="", started_at=started,
                    finished_at=started + timedelta(minutes=rng.randint(2, 6)), status="succeeded",
                    deployer=f"deploy-bot ({service.team})", change_notes=notes,
                ))
            cursor += timedelta(days=rng.uniform(3.0, 6.0))
    return deployments


def _baseline_config_changes(rng: random.Random, config: WorldConfig, faults: tuple[FaultInjection, ...]) -> list[ConfigChange]:
    changes: list[ConfigChange] = []
    nodes = [s.id for s in SERVICES] + [n.id for n in NODES if not n.external]
    for node in nodes:
        cursor = config.reference_start + timedelta(days=rng.uniform(1, 9))
        while cursor < config.reference_end:
            applied = cursor.replace(hour=rng.randint(8, 17), minute=rng.choice([0, 15, 30, 45]))
            if not _quiet(node, applied, faults):
                key, old, new, reason = rng.choice(BASELINE_CONFIG_KEYS)
                team = TEAM_OF_NODE.get(node, "keel")
                changes.append(ConfigChange(
                    id=PENDING_ID, node=node, key=key, old_value=old, new_value=new,
                    author=f"{team}-oncall", ticket=f"OPS-{rng.randint(100, 999)}", applied_at=applied, reason=reason,
                ))
            cursor += timedelta(days=rng.uniform(8, 16))
    return changes


def _baseline_alerts(rng: random.Random, config: WorldConfig, faults: tuple[FaultInjection, ...]) -> list[Alert]:
    alerts: list[Alert] = []
    nodes = [s.id for s in SERVICES] + [n.id for n in NODES if not n.external]
    cursor = config.reference_start + timedelta(hours=rng.uniform(6, 30))
    while cursor < config.reference_end:
        node = rng.choice(nodes)
        if not _quiet(node, cursor, faults):
            rule, summary = rng.choice(BASELINE_ALERT_RULES)
            alerts.append(Alert(
                id=PENDING_ID, rule=rule, node=node, severity="SEV3", fired_at=cursor,
                resolved_at=cursor + timedelta(minutes=rng.randint(10, 40)), summary=summary,
            ))
        cursor += timedelta(hours=rng.uniform(18, 54))
    return alerts


def _version_for(service: str, started: datetime, taken: set[tuple[str, str]]) -> str:
    """Next free vYYYY.MM.DD-n for the service that day; authored versions are reserved first."""
    day = started.strftime("%Y.%m.%d")
    n = 1
    while (service, f"v{day}-{n}") in taken:
        n += 1
    taken.add((service, f"v{day}-{n}"))
    return f"v{day}-{n}"


def generate_world(config: WorldConfig | None = None) -> GeneratedWorld:
    config = config or WorldConfig()
    faults = FAULTS
    rng = random.Random(config.seed)

    deployments = _baseline_deployments(rng, config, faults)
    config_changes = _baseline_config_changes(rng, config, faults)
    alerts = _baseline_alerts(rng, config, faults)

    manifest: dict[str, str] = {}
    pending: list[tuple[str | None, Deployment | ConfigChange | Alert]] = []
    pending += [(None, d) for d in deployments] + [(None, c) for c in config_changes] + [(None, a) for a in alerts]
    for fault in faults:
        for injected in fault.injected:
            pending.append((injected.handle, injected.record))

    dep_items = sorted(((h, r) for h, r in pending if isinstance(r, Deployment)), key=lambda x: (x[1].started_at, x[1].service))
    cfg_items = sorted(((h, r) for h, r in pending if isinstance(r, ConfigChange)), key=lambda x: (x[1].applied_at, x[1].node))
    alt_items = sorted(((h, r) for h, r in pending if isinstance(r, Alert)), key=lambda x: (x[1].fired_at, x[1].node))

    taken: set[tuple[str, str]] = {(d.service, d.version) for _, d in dep_items if isinstance(d, Deployment) and d.version}
    final_deployments: list[Deployment] = []
    for index, (handle, dep) in enumerate(dep_items, start=1):
        assert isinstance(dep, Deployment)
        version = dep.version or _version_for(dep.service, dep.started_at, taken)
        record = dep.model_copy(update={"id": f"DEP-{index:04d}", "version": version})
        final_deployments.append(record)
        if handle:
            manifest[handle] = record.id

    final_config: list[ConfigChange] = []
    for index, (handle, cfg) in enumerate(cfg_items, start=1):
        record = cfg.model_copy(update={"id": f"CFG-{index:04d}"})
        final_config.append(record)  # type: ignore[arg-type]
        if handle:
            manifest[handle] = record.id

    final_alerts: list[Alert] = []
    for index, (handle, alt) in enumerate(alt_items, start=1):
        record = alt.model_copy(update={"id": f"ALT-{index:04d}"})
        final_alerts.append(record)  # type: ignore[arg-type]
        if handle:
            manifest[handle] = record.id

    snapshot = WorldSnapshot(
        environment=ENVIRONMENT_NAME,
        dataset_label=DATASET_LABEL,
        seed=config.seed,
        reference_start=config.reference_start,
        reference_end=config.reference_end,
        services=list(SERVICES),
        nodes=list(NODES),
        edges=list(EDGES),
        deployments=final_deployments,
        config_changes=final_config,
        alerts=final_alerts,
        open_incidents=sorted((f.incident for f in faults), key=lambda i: i.detected_at),
        runbooks=corpus.load_runbooks(),
        historical_incidents=corpus.load_historical_incidents(),
        metric_effects=[e for f in faults for e in f.metric_effects],
        log_effects=[e for f in faults for e in f.log_effects],
        status_effects=[e for f in faults for e in f.status_effects],
    )
    return GeneratedWorld(snapshot=snapshot, manifest=MappingProxyType(dict(manifest)))


def build_snapshot(config: WorldConfig | None = None) -> WorldSnapshot:
    """Server-facing entry point: the snapshot only, no manifest."""
    return generate_world(config).snapshot
