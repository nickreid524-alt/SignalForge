"""Typed scenario specifications (evaluation ground truth)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from signalforge.reports.taxonomy import CauseCategory

# Shared, non-secret vocabulary; scenarios reuse the report taxonomy so evaluations compare like with like.
FailureCategory = CauseCategory

Difficulty = Literal["easy", "medium", "hard"]
PredicateKind = Literal[
    "record",           # an authored deployment/config/alert record exists (by handle)
    "metric_change",    # a metric changes by at least a ratio around a time
    "metric_stable",    # a metric does NOT change by more than a ratio around a time
    "metric_level",     # a metric is above/below a level at a time
    "log_pattern",      # a log pattern appears for a node in a window
    "node_status",      # get_service_health status for a node at a time
    "document",         # a runbook/incident is retrievable for a query (top-k)
    "dependency_edge",  # a topology edge exists
    "absence",          # no deployments/config changes for a node in a window
]


class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidencePredicate(ScenarioModel):
    kind: PredicateKind
    description: str
    handle: str | None = None
    node: str | None = None
    metric: str | None = None
    dimension: str | None = None
    at: datetime | None = None
    min_change_ratio: float | None = None
    max_change_ratio: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    pattern_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    status: str | None = None
    document_id: str | None = None
    query: str | None = None
    top_k: int = 5
    edge_id: str | None = None
    record_kind: Literal["deployments", "config_changes", "alerts"] | None = None


class MisleadingEvidence(ScenarioModel):
    """A plausible red herring the world deliberately contains."""

    description: str
    handle: str | None = None
    document_id: str | None = None
    node: str | None = None
    if_adopted_as_root_cause: Literal["unacceptable", "partial_credit"] = "unacceptable"


class UnacceptableConclusion(ScenarioModel):
    description: str
    category: FailureCategory | None = None
    handle: str | None = None
    document_id: str | None = None


class ScenarioSpec(ScenarioModel):
    id: str = Field(pattern=r"^SCN-\d{2}$")
    incident_id: str = Field(pattern=r"^INC-2026-01\d{2}$")
    title: str
    category: FailureCategory
    difficulty: Difficulty
    visible_service: str
    culprit_location: str
    root_cause_statement: str
    root_cause_handles: list[str] = []
    root_cause_terms: list[str] = []
    acceptable_categories: list[FailureCategory] = []
    decisive_evidence: list[EvidencePredicate] = []
    observable_evidence: list[EvidencePredicate] = []
    misleading_evidence: list[MisleadingEvidence] = Field(min_length=1)
    expected_useful_tools: list[str] = Field(min_length=1)
    unacceptable_conclusions: list[UnacceptableConclusion] = Field(min_length=1)
    injection_fixtures: list[str] = []
    max_confidence: float | None = None
    notes: str = ""

    @property
    def culprit_outside_visible_service(self) -> bool:
        return self.culprit_location != self.visible_service
