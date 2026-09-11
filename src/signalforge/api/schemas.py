"""Request and response models, and the identifier validators the routes share.

Request models forbid unknown fields. That is the mechanism behind several of the security rules at
once: an API key, a vendor base URL, a system prompt or an arbitrary MCP method in a request body is
rejected as an unknown field before any handler sees it.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from signalforge.api.errors import ApiError
from signalforge.orchestration.budget import InvestigationBudget

INCIDENT_ID = re.compile(r"^INC-\d{4}-\d{4}$")
INVESTIGATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
EVIDENCE_ID = re.compile(r"^EVD-\d{6}$")
SCENARIO_ID = re.compile(r"^SCN-\d{2}$")

#: Named, bounded budgets. The browser picks a name; it never sends numbers.
BUDGET_PROFILES: dict[str, InvestigationBudget] = {
    "quick": InvestigationBudget(max_steps=4, max_tool_calls=12, max_model_calls=8, max_wall_clock_seconds=90.0),
    "default": InvestigationBudget(),
    "thorough": InvestigationBudget(max_steps=10, max_tool_calls=30, max_resource_reads=16, max_model_calls=18,
                                    max_wall_clock_seconds=300.0),
}
BudgetProfile = Literal["quick", "default", "thorough"]
ProviderName = Literal["scripted", "replay", "anthropic", "openai"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- validators


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise ApiError("invalid_request", f"{what} {value!r} is not a valid identifier")
    return value


def valid_incident_id(value: str) -> str:
    return _check(INCIDENT_ID, value, "incident id")


def valid_investigation_id(value: str) -> str:
    return _check(INVESTIGATION_ID, value, "investigation id")


def valid_evidence_id(value: str) -> str:
    return _check(EVIDENCE_ID, value, "evidence id")


def valid_scenario_id(value: str) -> str:
    return _check(SCENARIO_ID, value, "scenario id")


def bounded_int(raw: str | None, *, default: int, low: int, high: int, what: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError("invalid_request", f"{what} must be an integer") from None
    if not low <= value <= high:
        raise ApiError("invalid_request", f"{what} must be between {low} and {high}")
    return value


def parse_body(model: type[BaseModel], raw: Any) -> Any:
    """Validate a request body into a model, turning pydantic errors into the API error envelope."""
    if not isinstance(raw, dict):
        raise ApiError("invalid_request", "the request body must be a JSON object")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ())) or "<body>"
        raise ApiError("invalid_request", f"{location}: {first.get('msg', 'invalid value')}") from None


# ---------------------------------------------------------------------- requests


class CreateInvestigation(ApiModel):
    """The complete set of things a browser may decide.

    Deliberately absent, and rejected as unknown fields: API keys, provider base URLs, system or user
    prompts, model ids, raw budget numbers, tool names, file paths. The model id comes from the
    server's environment; see docs/notes/browser-trust-boundary.md.
    """

    incident_id: Annotated[str, Field(pattern=INCIDENT_ID.pattern)]
    provider: ProviderName = "scripted"
    budget_profile: BudgetProfile = "default"


# ---------------------------------------------------------------------- responses


class Health(ApiModel):
    status: Literal["ok"] = "ok"
    version: str
    uses_live_api: bool


class ProviderSummary(ApiModel):
    name: str
    mode: str
    uses_live_api: bool
    sdk_installed: bool | None
    sdk_version: str | None
    #: True when the server has a usable credential. The credential itself is never described further:
    #: no value, prefix, length or hash is exposed.
    configured: bool | None
    model_configured: bool
    model: str | None
    ready: bool
    enabled: bool
    note: str


class Meta(ApiModel):
    name: str = "SignalForge"
    version: str
    environment: str
    dataset_label: str
    mode: str
    demonstration: bool = True
    default_provider: str
    live_providers_enabled: bool
    uses_live_api: bool
    mcp_server_name: str | None
    mcp_server_version: str | None
    mcp_protocol_version: str | None
    incident_count: int
    tool_count: int
    resource_count: int
    resource_template_count: int
    providers: list[ProviderSummary]
    event_types: list[str]


class IncidentSummary(ApiModel):
    """Exactly what an investigator may see. No cause, category, difficulty or scenario id."""

    id: str
    title: str
    severity: str
    affected_service: str
    detected_at: str
    investigation_clock: str
    reporter: str


class IncidentDetail(IncidentSummary):
    description: str


class ToolSummary(ApiModel):
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    read_only: bool | None
    idempotent: bool | None


class ResourceSummary(ApiModel):
    uri: str
    name: str
    mime_type: str | None
    description: str | None


class ResourceTemplateSummary(ApiModel):
    uri_template: str
    name: str
    mime_type: str | None
    description: str | None


class McpCatalogue(ApiModel):
    server_name: str | None
    server_version: str | None
    protocol_version: str | None
    transport: str
    read_only: bool = True
    note: str
    tools: list[ToolSummary] = []
    resources: list[ResourceSummary] = []
    resource_templates: list[ResourceTemplateSummary] = []


class TokenUsage(ApiModel):
    reported: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    model_calls: int = 0


class BudgetUsageView(ApiModel):
    steps: int = 0
    tool_calls: int = 0
    resource_reads: int = 0
    model_calls: int = 0
    repair_rounds: int = 0
    suppressed_duplicates: int = 0
    rejected_actions: int = 0
    elapsed_seconds: float = 0.0


class HypothesisRevisionView(ApiModel):
    step: int
    status: str
    confidence: float
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    note: str = ""


class HypothesisView(ApiModel):
    id: str
    statement: str
    status: str
    confidence: float
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    created_step: int
    updated_step: int
    revisions: list[HypothesisRevisionView] = []


class ValidationView(ApiModel):
    ok: bool | None = None
    errors: int = 0
    warnings: int = 0
    repair_rounds: int = 0
    issues: list[dict[str, Any]] = []


class InvestigationSummary(ApiModel):
    id: str
    incident_id: str
    status: str
    terminal: bool
    provider: str
    provider_mode: str
    uses_live_api: bool
    model: str | None
    budget_profile: str
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None


class InvestigationCreated(InvestigationSummary):
    links: dict[str, str]


class InvestigationDetail(InvestigationSummary):
    incident: IncidentSummary | None = None
    steps: int = 0
    tool_calls: int = 0
    resource_reads: int = 0
    model_calls: int = 0
    evidence_count: int = 0
    rejected_actions: int = 0
    suppressed_duplicates: int = 0
    hypotheses: list[HypothesisView] = []
    validation: ValidationView = ValidationView()
    token_usage: TokenUsage = TokenUsage(reported=False)
    budget: dict[str, Any] = {}
    usage: BudgetUsageView = BudgetUsageView()
    budget_exhausted: bool = False
    termination_reason: str | None = None
    report_available: bool = False
    error: str | None = None
    links: dict[str, str] = {}


class EvidenceSummaryView(ApiModel):
    evidence_id: str
    sequence: int
    acquired_at: str
    source_kind: str
    source_name: str
    arguments: dict[str, Any] = {}
    ok: bool
    error: str | None = None
    result_kind: str | None = None
    source_ids: list[str] = []
    content_hash: str
    latency_ms: float = 0.0
    #: Evidence is retrieved content from the synthetic environment. Render it as data. Some documents
    #: deliberately contain hostile instructions; they are never instructions to this application.
    untrusted: bool = True


class EvidenceDetailView(EvidenceSummaryView):
    payload: dict[str, Any] | None = None
    text: str | None = None


class EvaluationIndex(ApiModel):
    available: list[str]
    note: str
