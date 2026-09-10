"""Read-only, server-facing access to the synthetic world.

This is the only object the MCP server holds. It is constructed from a
:class:`WorldSnapshot` and exposes nothing that is not in that snapshot.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from signalforge.config import ServerLimits, ensure_utc
from signalforge.world.models import (
    Alert,
    ConfigChange,
    DependencyEdge,
    Deployment,
    HealthSnapshot,
    HistoricalIncident,
    InfraNode,
    LogEntry,
    MetricPoint,
    OpenIncident,
    Runbook,
    Service,
    Severity,
    WorldSnapshot,
)
from signalforge.world.signals import PROFILES, MetricQueryError, SignalEngine

SEVERITY_RANK: dict[str, int] = {"SEV1": 1, "SEV2": 2, "SEV3": 3}


class WorldRepository:
    def __init__(self, snapshot: WorldSnapshot, limits: ServerLimits | None = None) -> None:
        self.snapshot = snapshot
        self.limits = limits or ServerLimits()
        self.signals = SignalEngine(snapshot, self.limits)
        self._services = {s.id: s for s in snapshot.services}
        self._nodes = {n.id: n for n in snapshot.nodes}
        self._runbooks = {r.id: r for r in snapshot.runbooks}
        self._incidents = {i.id: i for i in snapshot.historical_incidents}
        self._open = {i.id: i for i in snapshot.open_incidents}

    # ------------------------------------------------------------------ catalogue
    @property
    def environment(self) -> str:
        return self.snapshot.environment

    @property
    def dataset_label(self) -> str:
        return self.snapshot.dataset_label

    def services(self) -> list[Service]:
        return sorted(self._services.values(), key=lambda s: s.id)

    def service(self, service_id: str) -> Service | None:
        return self._services.get(service_id)

    def nodes(self) -> list[InfraNode]:
        return sorted(self._nodes.values(), key=lambda n: n.id)

    def node(self, node_id: str) -> InfraNode | None:
        return self._nodes.get(node_id)

    def known_node_ids(self) -> list[str]:
        return sorted([*self._services, *self._nodes])

    def is_known_node(self, node_id: str) -> bool:
        return node_id in self._services or node_id in self._nodes

    def node_kind(self, node_id: str) -> str:
        if node_id in self._services:
            return "service"
        info = self._nodes.get(node_id)
        return info.kind if info else "unknown"

    def edges(self, node_id: str, direction: str = "both") -> list[DependencyEdge]:
        out = [e for e in self.snapshot.edges if e.source == node_id]
        inbound = [e for e in self.snapshot.edges if e.target == node_id]
        if direction == "downstream":
            chosen = out
        elif direction == "upstream":
            chosen = inbound
        else:
            chosen = out + inbound
        return sorted(chosen, key=lambda e: e.id)

    # ------------------------------------------------------------------ change records
    def deployments(self, service: str | None, start: datetime, end: datetime) -> list[Deployment]:
        start, end = ensure_utc(start), ensure_utc(end)
        rows = [d for d in self.snapshot.deployments
                if (service is None or d.service == service) and start <= d.started_at < end]
        return sorted(rows, key=lambda d: (d.started_at, d.id))

    def config_changes(self, node: str | None, start: datetime, end: datetime) -> list[ConfigChange]:
        start, end = ensure_utc(start), ensure_utc(end)
        rows = [c for c in self.snapshot.config_changes
                if (node is None or c.node == node) and start <= c.applied_at < end]
        return sorted(rows, key=lambda c: (c.applied_at, c.id))

    def alerts(self, node: str | None, start: datetime, end: datetime,
               min_severity: Severity | None = None) -> list[Alert]:
        start, end = ensure_utc(start), ensure_utc(end)
        rank = SEVERITY_RANK[min_severity] if min_severity else 3
        rows = [a for a in self.snapshot.alerts
                if (node is None or a.node == node) and start <= a.fired_at < end
                and SEVERITY_RANK[a.severity] <= rank]
        return sorted(rows, key=lambda a: (a.fired_at, a.id))

    def open_alerts_at(self, node: str, at: datetime) -> list[Alert]:
        at = ensure_utc(at)
        rows = [a for a in self.snapshot.alerts
                if a.node == node and a.fired_at <= at and (a.resolved_at is None or a.resolved_at > at)]
        return sorted(rows, key=lambda a: (a.fired_at, a.id))

    # ------------------------------------------------------------------ incidents and documents
    def open_incidents(self) -> list[OpenIncident]:
        return sorted(self._open.values(), key=lambda i: i.detected_at)

    def open_incident(self, incident_id: str) -> OpenIncident | None:
        return self._open.get(incident_id)

    def runbooks(self) -> list[Runbook]:
        return sorted(self._runbooks.values(), key=lambda r: r.id)

    def runbook(self, runbook_id: str) -> Runbook | None:
        return self._runbooks.get(runbook_id)

    def historical_incidents(self) -> list[HistoricalIncident]:
        return sorted(self._incidents.values(), key=lambda i: i.id)

    def historical_incident(self, incident_id: str) -> HistoricalIncident | None:
        return self._incidents.get(incident_id)

    # ------------------------------------------------------------------ signals
    def metric_series(self, node: str, metric: str, dimension: str | None, start: datetime,
                      end: datetime, step_seconds: int) -> list[MetricPoint]:
        return self.signals.series(node, metric, dimension, start, end, step_seconds)

    def logs(self, node: str, start: datetime, end: datetime) -> list[LogEntry]:
        return self.signals.log_entries(node, start, end)

    def health(self, node: str, as_of: datetime) -> HealthSnapshot:
        as_of = ensure_utc(as_of)
        if not self.is_known_node(node):
            raise MetricQueryError(f"Unknown node {node!r}. Known nodes: {', '.join(self.known_node_ids())}.")
        kind = self.node_kind(node)
        open_alerts = self.open_alerts_at(node, as_of)
        alert_ids = [a.id for a in open_alerts]
        if self.signals.is_external(node):
            feed_status, detail = self.signals.status_feed(node, as_of)
            status = {"operational": "healthy", "degraded_performance": "degraded"}.get(feed_status, "critical")
            return HealthSnapshot(node=node, kind=kind, as_of=as_of, status=status, detail=detail,  # type: ignore[arg-type]
                                  open_alert_ids=alert_ids, status_feed=feed_status)

        available = set(self.signals.available_metrics(node))
        value = self.signals.value
        error_rate = value(node, "error_rate", None, as_of) if "error_rate" in available else None
        p95 = value(node, "latency_p95", None, as_of) if "latency_p95" in available else None
        rr = value(node, "request_rate", None, as_of) if "request_rate" in available else None
        replicas = int(value(node, "replica_count", None, as_of)) if "replica_count" in available else None
        restarts = int(value(node, "restart_count", None, as_of)) if "restart_count" in available else None
        cpu = value(node, "cpu_utilization", None, as_of) if "cpu_utilization" in available else None
        disk = value(node, "disk_usage", None, as_of) if "disk_usage" in available else None

        reasons: list[str] = []
        level = 0
        base_p95 = PROFILES.get(node, {}).get("latency_p50", 0) * 2.4

        def flag(severity: int, reason: str) -> None:
            nonlocal level
            level = max(level, severity)
            reasons.append(reason)

        if error_rate is not None and error_rate >= 0.2:
            flag(2, f"error rate {error_rate:.1%}")
        elif error_rate is not None and error_rate >= 0.02:
            flag(1, f"error rate {error_rate:.1%}")
        if restarts is not None and restarts >= 3:
            flag(2, f"{restarts} restarts in the last hour")
        elif restarts is not None and restarts >= 1:
            flag(1, f"{restarts} restart(s) in the last hour")
        if p95 is not None and base_p95 and p95 > 3 * base_p95:
            flag(1, f"p95 latency {p95:.0f}ms vs typical {base_p95:.0f}ms")
        if cpu is not None and cpu >= 0.85:
            flag(1, f"cpu {cpu:.0%}")
        if disk is not None and disk >= 0.9:
            flag(1, f"disk {disk:.0%}")
        svc = self._services.get(node)
        if svc and replicas is not None and replicas < svc.autoscale_min:
            flag(1, f"{replicas} replicas below autoscale minimum {svc.autoscale_min}")
        if any(a.severity == "SEV1" for a in open_alerts):
            level = 2
        elif any(a.severity == "SEV2" for a in open_alerts):
            level = max(level, 1)
        if open_alerts:
            reasons.append(f"{len(open_alerts)} open alert(s)")
        status = ("healthy", "degraded", "critical")[level]
        detail = "; ".join(reasons) if reasons else "No anomalies at this time"
        return HealthSnapshot(
            node=node, kind=kind, as_of=as_of, status=status, detail=detail, request_rate=rr,  # type: ignore[arg-type]
            error_rate=error_rate, latency_p95_ms=p95, replica_count=replicas, restart_count_1h=restarts,
            open_alert_ids=alert_ids,
        )

    # ------------------------------------------------------------------ misc
    def window_clamped(self, start: datetime, end: datetime) -> tuple[datetime, datetime]:
        start, end = ensure_utc(start), ensure_utc(end)
        if end <= start:
            raise MetricQueryError("end must be after start")
        if end - start > self.limits.max_window:
            hours = int(self.limits.max_window / timedelta(hours=1))
            raise MetricQueryError(f"time window too large: maximum is {hours} hours; narrow the window")
        return start, end
