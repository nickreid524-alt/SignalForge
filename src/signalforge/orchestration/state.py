"""Explicit investigation state and legal transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from signalforge.orchestration.budget import BudgetUsage, InvestigationBudget
from signalforge.orchestration.hypotheses import HypothesisBoard


class InvestigationStatus(StrEnum):
    CREATED = "created"
    SEEDING = "seeding"
    DELIBERATING = "deliberating"
    GATHERING = "gathering"
    CONCLUDING = "concluding"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED_VALIDATION = "failed_validation"
    FAILED = "failed"


S = InvestigationStatus

TERMINAL_STATUSES: frozenset[InvestigationStatus] = frozenset(
    {S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.FAILED_VALIDATION, S.FAILED}
)

# The only legal moves. Anything else is a programming error, not a runtime branch.
TRANSITIONS: dict[InvestigationStatus, frozenset[InvestigationStatus]] = {
    S.CREATED: frozenset({S.SEEDING, S.FAILED}),
    S.SEEDING: frozenset({S.DELIBERATING, S.FAILED}),
    S.DELIBERATING: frozenset({S.GATHERING, S.CONCLUDING, S.FAILED}),
    S.GATHERING: frozenset({S.DELIBERATING, S.CONCLUDING, S.FAILED}),
    S.CONCLUDING: frozenset({S.VALIDATING, S.FAILED}),
    S.VALIDATING: frozenset({S.COMPLETED, S.COMPLETED_WITH_WARNINGS, S.REPAIRING, S.FAILED_VALIDATION, S.FAILED}),
    S.REPAIRING: frozenset({S.VALIDATING, S.FAILED}),
    S.COMPLETED: frozenset(),
    S.COMPLETED_WITH_WARNINGS: frozenset(),
    S.FAILED_VALIDATION: frozenset(),
    S.FAILED: frozenset(),
}


class IllegalTransition(RuntimeError):
    pass


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionOutcome(StateModel):
    request_id: str
    kind: str
    name: str
    arguments: dict[str, Any] = {}
    accepted: bool
    rejection_code: str | None = None
    rejection_reason: str | None = None
    evidence_id: str | None = None
    duplicate_of: str | None = None
    ok: bool | None = None
    error: str | None = None
    latency_ms: float = 0.0


class StatusChange(StateModel):
    at: datetime
    from_status: InvestigationStatus
    to_status: InvestigationStatus
    note: str = ""


class StepRecord(StateModel):
    step: int
    started_at: datetime
    ended_at: datetime | None = None
    model_call_id: str | None = None
    assistant_text: str | None = None
    actions: list[ActionOutcome] = []
    finish_requested: bool = False


class IncidentBrief(StateModel):
    """The open incident as read through MCP (incidents://open). Contains no cause information."""

    id: str
    title: str
    description: str
    detected_at: datetime
    severity: str
    affected_service: str
    reporter: str
    investigation_clock: datetime


class InvestigationState(StateModel):
    investigation_id: str
    incident_id: str
    status: InvestigationStatus = S.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    incident: IncidentBrief | None = None
    budget: InvestigationBudget = InvestigationBudget()
    usage: BudgetUsage = BudgetUsage()
    hypotheses: HypothesisBoard = HypothesisBoard()
    steps: list[StepRecord] = []
    history: list[StatusChange] = []
    seen_calls: dict[str, str] = Field(default_factory=dict, description="canonical call key -> evidence id")
    evidence_index: list[str] = Field(default_factory=list,
                                      description="one line per gathered evidence item, for status blocks")
    finish_requested: bool = False
    finish_reason: str | None = None
    termination_reason: str | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def transition(state: InvestigationState, to_status: InvestigationStatus, note: str = "",
               now: datetime | None = None) -> InvestigationState:
    """Move the state machine along a legal edge (mutates and returns ``state``)."""
    if to_status not in TRANSITIONS[state.status]:
        raise IllegalTransition(f"{state.status.value} -> {to_status.value} is not a legal transition")
    at = now or datetime.now(UTC)
    state.history.append(StatusChange(at=at, from_status=state.status, to_status=to_status, note=note))
    state.status = to_status
    state.updated_at = at
    return state
