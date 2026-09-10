"""The canonical investigation report is typed data. Markdown is a rendering of it, never the source."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from signalforge.providers.base import ProviderInfo
from signalforge.reports.taxonomy import CauseCategory

ClaimKind = Literal["OBSERVED", "INFERRED", "UNKNOWN"]
ReportStatus = Literal["root_cause_identified", "probable_cause", "inconclusive"]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(ReportModel):
    statement: str = Field(min_length=3, max_length=600)
    kind: ClaimKind
    evidence_ids: list[str] = Field(default_factory=list, description="EVD-000004 or EVD-000004#RECORD citations.")


class ReportHypothesis(ReportModel):
    id: str = Field(pattern=r"^H\d+$")
    statement: str = Field(min_length=3, max_length=600)
    category: CauseCategory
    status: Literal["supported", "refuted", "inconclusive"]
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    reasoning: str = Field(default="", max_length=800)


class RecommendedAction(ReportModel):
    action: str = Field(min_length=3, max_length=400)
    rationale: str = Field(min_length=3, max_length=600)
    priority: Literal["P1", "P2", "P3"]
    kind: Literal["mitigation", "diagnostic", "follow_up"]
    evidence_ids: list[str] = []


class ReportDraft(ReportModel):
    """What a provider produces. The orchestrator wraps it into an InvestigationReport."""

    summary: str = Field(min_length=10, max_length=2000)
    status: ReportStatus
    primary_hypothesis: ReportHypothesis | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    hypotheses_considered: list[ReportHypothesis] = Field(min_length=1)
    key_findings: list[Claim] = Field(min_length=1)
    contradicting_evidence: list[Claim] = []
    recommended_actions: list[RecommendedAction] = []
    unknowns: list[Claim] = []
    limitations: list[str] = []


class ValidationIssue(ReportModel):
    rule: str
    severity: Literal["error", "warning", "info"]
    path: str
    message: str


class ValidationResult(ReportModel):
    issues: list[ValidationIssue] = []

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors


class EvidenceSummary(ReportModel):
    evidence_id: str
    source_kind: str
    source_name: str
    result_kind: str | None
    record_count: int
    ok: bool
    cited: bool


class TokenUsageSummary(ReportModel):
    """Provider-reported token usage. ``reported=False`` means the provider uses no LLM (scripted / replay)."""

    reported: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_output_tokens: int = 0
    model_calls: int = 0


class InvestigationReport(ReportDraft):
    """Draft plus everything the orchestrator knows: identity, provider, budget use, validation, evidence index."""

    investigation_id: str
    incident_id: str
    affected_service: str
    provider: ProviderInfo
    terminal_status: str
    validation: ValidationResult
    repair_rounds: int
    steps_used: int
    tool_calls_used: int
    resource_reads_used: int
    model_calls_used: int
    suppressed_duplicate_calls: int
    rejected_actions: int
    investigation_duration_seconds: float
    budget_exhausted: bool
    termination_reason: str | None
    evidence_index: list[EvidenceSummary]
    token_usage: TokenUsageSummary = TokenUsageSummary()
