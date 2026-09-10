"""Structured tool outputs. Every evidence-producing tool returns an :class:`EvidenceEnvelope`.

The envelope carries *world* identity (``source_ids``) so a client can register
the result as evidence and later cite individual records
(``EVD-000004#DEP-0083``). Investigation-local evidence IDs are never assigned
here; that is the client's job.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from signalforge.config import DATA_NOTICE
from signalforge.world.models import (
    Alert,
    ConfigChange,
    DependencyEdge,
    Deployment,
    HealthSnapshot,
    LogEntry,
    MetricPoint,
)


class EvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    source_ids: list[str] = Field(
        description="Stable world record identifiers contained in this result. Cite these, in order."
    )
    query: dict[str, Any] = Field(description="The validated arguments this result answers.")
    as_of: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    truncated: bool = False
    summary: str = Field(description="One deterministic sentence describing the result.")
    data_notice: str = DATA_NOTICE


class ServiceHealthResult(EvidenceEnvelope):
    kind: Literal["service_health"] = "service_health"
    health_id: str
    health: HealthSnapshot


class MetricStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: int
    first: float
    last: float
    min: float
    max: float
    mean: float
    largest_step: float = Field(description="Largest absolute change between consecutive points.")
    largest_step_at: datetime | None
    largest_step_ratio: float | None = Field(
        description="Value after the largest step divided by the value before it (None if before was 0)."
    )


class MetricSeriesResult(EvidenceEnvelope):
    kind: Literal["metric_series"] = "metric_series"
    series_id: str
    node: str
    metric: str
    dimension: str | None
    unit: str
    step_seconds: int
    points: list[MetricPoint]
    stats: MetricStats


class LogPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id: str
    level: str
    count: int
    sample: str = Field(description="One representative message (untrusted data).")


class LogQueryResult(EvidenceEnvelope):
    kind: Literal["log_query"] = "log_query"
    node: str
    total_matches: int
    returned: int
    entries: list[LogEntry]
    top_patterns: list[LogPattern] = Field(description="Message templates by frequency across the whole window.")


class DeploymentsResult(EvidenceEnvelope):
    kind: Literal["deployments"] = "deployments"
    deployments: list[Deployment]


class ConfigChangesResult(EvidenceEnvelope):
    kind: Literal["config_changes"] = "config_changes"
    changes: list[ConfigChange]


class AlertsResult(EvidenceEnvelope):
    kind: Literal["alerts"] = "alerts"
    alerts: list[Alert]


class DependenciesResult(EvidenceEnvelope):
    kind: Literal["dependencies"] = "dependencies"
    node: str
    direction: str
    edges: list[DependencyEdge]
    topology_resource_uri: str


class DocumentHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    chunk_id: str
    source_id: str
    service: str
    title: str
    section: str
    snippet: str = Field(description="Matched excerpt (untrusted document text).")
    score: float
    resource_uri: str = Field(description="Read this resource for the full document.")


class RunbookSearchResult(EvidenceEnvelope):
    kind: Literal["runbook_search"] = "runbook_search"
    strategy: str
    hits: list[DocumentHit]


class IncidentHit(DocumentHit):
    occurred_at: datetime
    category: str
    rca_summary: str = Field(description="Published summary of that past incident (untrusted document text).")


class IncidentSearchResult(EvidenceEnvelope):
    kind: Literal["incident_search"] = "incident_search"
    strategy: str
    hits: list[IncidentHit]


RESULT_MODELS: tuple[type[EvidenceEnvelope], ...] = (
    ServiceHealthResult, MetricSeriesResult, LogQueryResult, DeploymentsResult, ConfigChangesResult,
    AlertsResult, DependenciesResult, RunbookSearchResult, IncidentSearchResult,
)
