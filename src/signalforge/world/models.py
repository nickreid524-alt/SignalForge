"""Typed records of the synthetic world. Every record carries a stable world ID."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["SEV1", "SEV2", "SEV3"]
LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]
NodeKind = Literal["service", "database", "cache", "message_bus", "search", "dns", "external"]
Criticality = Literal["critical", "important", "best_effort"]
DeploymentStatus = Literal["succeeded", "failed", "rolled_back"]

MetricName = Literal[
    "request_rate",
    "error_rate",
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "cpu_utilization",
    "memory_usage",
    "disk_usage",
    "db_pool_active",
    "db_pool_max",
    "cache_hit_ratio",
    "cache_evictions",
    "queue_lag",
    "queue_depth",
    "replica_count",
    "restart_count",
    "dependency_latency_p95",
    "http_status_count",
    "redelivery_count",
    "connections_by_client",
]

METRIC_UNITS: dict[str, str] = {
    "request_rate": "requests/s",
    "error_rate": "ratio",
    "latency_p50": "ms",
    "latency_p95": "ms",
    "latency_p99": "ms",
    "cpu_utilization": "ratio",
    "memory_usage": "MiB",
    "disk_usage": "ratio",
    "db_pool_active": "connections",
    "db_pool_max": "connections",
    "cache_hit_ratio": "ratio",
    "cache_evictions": "keys/min",
    "queue_lag": "seconds",
    "queue_depth": "messages",
    "replica_count": "replicas",
    "restart_count": "restarts/h",
    "dependency_latency_p95": "ms",
    "http_status_count": "responses/min",
    "redelivery_count": "messages/min",
    "connections_by_client": "connections",
}


class WorldModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Service(WorldModel):
    id: str
    name: str
    team: str
    tier: int = Field(ge=1, le=3)
    runtime: str
    replicas: int
    autoscale_min: int
    autoscale_max: int
    datastores: list[str] = []
    description: str


class InfraNode(WorldModel):
    id: str
    name: str
    kind: NodeKind
    external: bool = False
    description: str


class DependencyEdge(WorldModel):
    id: str
    source: str
    target: str
    protocol: str
    criticality: Criticality
    timeout_ms: int


class Deployment(WorldModel):
    id: str
    service: str
    version: str
    started_at: datetime
    finished_at: datetime
    status: DeploymentStatus = "succeeded"
    deployer: str
    change_notes: list[str]
    rollback_of: str | None = None


class ConfigChange(WorldModel):
    id: str
    node: str
    key: str
    old_value: str
    new_value: str
    author: str
    ticket: str
    applied_at: datetime
    reason: str


class Alert(WorldModel):
    id: str
    rule: str
    node: str
    severity: Severity
    fired_at: datetime
    resolved_at: datetime | None = None
    summary: str


class OpenIncident(WorldModel):
    """An incident awaiting investigation. Deliberately carries no cause information."""

    id: str
    title: str
    description: str
    detected_at: datetime
    severity: Severity
    affected_service: str
    reporter: str
    investigation_clock: datetime


class Runbook(WorldModel):
    id: str
    title: str
    service: str  # a service id, an infra node id, or "platform"
    tags: list[str] = []
    updated: str
    body: str  # markdown, untrusted data


class HistoricalIncident(WorldModel):
    """A resolved incident from the past with its published post-incident review."""

    id: str
    title: str
    service: str
    severity: Severity
    occurred_at: datetime
    resolved_at: datetime
    category: str
    rca_summary: str
    body: str  # markdown, untrusted data


class MetricEffect(WorldModel):
    """A perturbation of a procedural metric over a time window."""

    node: str
    metric: str  # a MetricName or the group name "latency" (applies to p50/p95/p99)
    dimension: str | None = None
    start: datetime
    end: datetime | None = None
    shape: Literal["step", "ramp", "sawtooth"]
    multiplier: float | None = None
    absolute: float | None = None
    ramp_end: datetime | None = None
    period_seconds: int | None = None
    low: float | None = None
    high: float | None = None


class LogEffect(WorldModel):
    """Extra log lines emitted by a node over a time window."""

    node: str
    start: datetime
    end: datetime | None = None
    level: LogLevel
    template: str
    pattern_id: str
    rate_per_bucket: float = Field(gt=0, le=100)


class StatusEffect(WorldModel):
    """Status-feed state of an external dependency over a time window."""

    node: str
    start: datetime
    end: datetime | None = None
    status: Literal["operational", "degraded_performance", "partial_outage", "major_outage"]
    detail: str


class WorldSnapshot(WorldModel):
    """Everything the MCP server may read. Contains no evaluation ground truth."""

    environment: str
    dataset_label: str
    seed: int
    reference_start: datetime
    reference_end: datetime
    services: list[Service]
    nodes: list[InfraNode]
    edges: list[DependencyEdge]
    deployments: list[Deployment]
    config_changes: list[ConfigChange]
    alerts: list[Alert]
    open_incidents: list[OpenIncident]
    runbooks: list[Runbook]
    historical_incidents: list[HistoricalIncident]
    metric_effects: list[MetricEffect]
    log_effects: list[LogEffect]
    status_effects: list[StatusEffect]


class LogEntry(WorldModel):
    id: str
    node: str
    timestamp: datetime
    level: LogLevel
    message: str
    pattern_id: str


class MetricPoint(WorldModel):
    timestamp: datetime
    value: float


class HealthSnapshot(WorldModel):
    node: str
    kind: NodeKind
    as_of: datetime
    status: Literal["healthy", "degraded", "critical", "unknown"]
    detail: str
    request_rate: float | None = None
    error_rate: float | None = None
    latency_p95_ms: float | None = None
    replica_count: int | None = None
    restart_count_1h: int | None = None
    open_alert_ids: list[str] = []
    status_feed: str | None = None
