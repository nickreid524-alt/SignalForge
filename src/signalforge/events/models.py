"""Typed investigation events: the schema a user interface is allowed to depend on.

Every event type has exactly one payload model (``EVENT_PAYLOADS``), and events can only be built
through :meth:`InvestigationEvent.build`, which refuses a payload of the wrong type. Payload models
forbid extra fields, so an accidental leak (a provider object, a raw trace row, hidden reasoning)
fails loudly instead of reaching a browser.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    INVESTIGATION_CREATED = "investigation.created"
    STATUS_CHANGED = "status.changed"
    STEP_STARTED = "step.started"
    PROVIDER_COMPLETED = "provider.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    TOOL_REJECTED = "tool.rejected"
    RESOURCE_READ = "resource.read"
    EVIDENCE_REGISTERED = "evidence.registered"
    HYPOTHESIS_UPDATED = "hypothesis.updated"
    VALIDATION_STARTED = "validation.started"
    VALIDATION_FAILED = "validation.failed"
    REPAIR_STARTED = "repair.started"
    REPORT_COMPLETED = "report.completed"
    INVESTIGATION_COMPLETED = "investigation.completed"
    INVESTIGATION_FAILED = "investigation.failed"


TERMINAL_EVENTS: frozenset[EventType] = frozenset(
    {EventType.INVESTIGATION_COMPLETED, EventType.INVESTIGATION_FAILED}
)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvestigationCreated(Payload):
    incident_id: str
    incident_title: str = ""
    affected_service: str = ""
    provider: str
    provider_mode: str
    uses_live_api: bool
    model: str | None = None
    budget_profile: str = "default"


class StatusChanged(Payload):
    from_status: str
    to_status: str
    note: str = ""


class StepStarted(Payload):
    step: int
    budget_remaining: dict[str, int] = {}


class ProviderCompleted(Payload):
    step: int
    purpose: str
    provider: str
    model: str | None = None
    stop_reason: str | None = None
    latency_ms: float = 0.0
    text_preview: str = ""
    tool_requests: int = 0
    usage_reported: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None


class ToolRequested(Payload):
    step: int
    request_id: str
    name: str
    arguments: dict[str, Any] = {}


class ToolCompleted(Payload):
    step: int
    request_id: str
    name: str
    evidence_id: str | None = None
    ok: bool
    error: str | None = None
    latency_ms: float = 0.0


class ToolRejected(Payload):
    """An action the provider asked for that the policy refused. The headline prompt-injection signal."""

    step: int
    request_id: str
    name: str
    code: str
    reason: str
    duplicate_of: str | None = None


class ResourceRead(Payload):
    step: int
    request_id: str
    uri: str
    evidence_id: str | None = None
    ok: bool
    error: str | None = None


class EvidenceRegistered(Payload):
    evidence_id: str
    sequence: int
    source_kind: str
    source_name: str
    result_kind: str | None = None
    record_count: int = 0
    ok: bool
    #: Evidence bodies are retrieved content. They are data to display, never instructions to act on.
    untrusted: bool = True


class HypothesisUpdated(Payload):
    hypothesis_id: str
    step: int
    statement: str
    status: str
    confidence: float
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    note: str = ""


class ValidationStarted(Payload):
    round: int


class ValidationFailed(Payload):
    round: int
    errors: int
    warnings: int
    rules: list[str] = []


class RepairStarted(Payload):
    round: int
    reason: str = ""


class ReportCompleted(Payload):
    status: str
    confidence: float
    primary_hypothesis_id: str | None = None
    validation_ok: bool
    repair_rounds: int = 0


class InvestigationCompleted(Payload):
    terminal_status: str
    steps: int
    tool_calls: int
    resource_reads: int
    evidence_count: int
    rejected_actions: int
    duration_seconds: float
    termination_reason: str | None = None
    has_report: bool = True


class InvestigationFailed(Payload):
    terminal_status: str
    message: str
    category: str | None = None
    duration_seconds: float = 0.0


EVENT_PAYLOADS: dict[EventType, type[Payload]] = {
    EventType.INVESTIGATION_CREATED: InvestigationCreated,
    EventType.STATUS_CHANGED: StatusChanged,
    EventType.STEP_STARTED: StepStarted,
    EventType.PROVIDER_COMPLETED: ProviderCompleted,
    EventType.TOOL_REQUESTED: ToolRequested,
    EventType.TOOL_COMPLETED: ToolCompleted,
    EventType.TOOL_REJECTED: ToolRejected,
    EventType.RESOURCE_READ: ResourceRead,
    EventType.EVIDENCE_REGISTERED: EvidenceRegistered,
    EventType.HYPOTHESIS_UPDATED: HypothesisUpdated,
    EventType.VALIDATION_STARTED: ValidationStarted,
    EventType.VALIDATION_FAILED: ValidationFailed,
    EventType.REPAIR_STARTED: RepairStarted,
    EventType.REPORT_COMPLETED: ReportCompleted,
    EventType.INVESTIGATION_COMPLETED: InvestigationCompleted,
    EventType.INVESTIGATION_FAILED: InvestigationFailed,
}


class InvestigationEvent(BaseModel):
    """One application event. ``seq`` is monotonic and gap-free within an investigation, starting at 1."""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    investigation_id: str
    type: EventType
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = {}

    @classmethod
    def build(cls, *, seq: int, investigation_id: str, event_type: EventType, payload: Payload,
              at: datetime | None = None) -> InvestigationEvent:
        expected = EVENT_PAYLOADS[event_type]
        if not isinstance(payload, expected):
            raise TypeError(f"{event_type.value} expects {expected.__name__}, got {type(payload).__name__}")
        return cls(seq=seq, investigation_id=investigation_id, type=event_type,
                   at=at or datetime.now(UTC), payload=payload.model_dump(mode="json"))

    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_EVENTS
