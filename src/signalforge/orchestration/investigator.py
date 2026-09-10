"""The investigation engine: one bounded loop, typed state, real MCP calls, grounded output.

    created -> seeding -> (deliberating <-> gathering)* -> concluding -> validating (-> repairing -> validating)*
            -> completed | completed_with_warnings | failed_validation | failed
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import anyio
from pydantic import BaseModel

from signalforge.audit.store import TraceStore
from signalforge.evidence.citations import CitationValidator, parse_citation
from signalforge.evidence.models import EvidenceItem
from signalforge.evidence.registry import EvidenceRegistry
from signalforge.mcp_client.client import OpsClient
from signalforge.orchestration.actions import (
    CallTool,
    FinishInvestigation,
    ReadResource,
    UpdateHypotheses,
    local_action_specs,
)
from signalforge.orchestration.budget import InvestigationBudget, deliberation_exhausted
from signalforge.orchestration.policy import ActionPolicy, PolicyDecision, call_key, resource_key
from signalforge.orchestration.rendering import (
    SYSTEM_PROMPT,
    render_evidence,
    render_hypotheses_result,
    render_rejection,
    render_report_request,
    render_seed,
    render_validation_failure,
)
from signalforge.orchestration.state import (
    TERMINAL_STATUSES,
    ActionOutcome,
    IncidentBrief,
    InvestigationState,
    InvestigationStatus,
    StepRecord,
    transition,
)
from signalforge.providers.base import (
    AssistantMessage,
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelProvider,
    ModelTurn,
    ProviderError,
    StructuredResult,
    SystemPrompt,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
    request_fingerprint,
)
from signalforge.reports.schema import (
    EvidenceSummary,
    InvestigationReport,
    ReportDraft,
    ValidationIssue,
    ValidationResult,
)
from signalforge.reports.validation import GroundingValidator

S = InvestigationStatus


class InvestigationError(RuntimeError):
    pass


@dataclass
class ReportRound:
    round_index: int
    draft: ReportDraft | None
    validation: ValidationResult
    parse_error: str | None = None


@dataclass
class InvestigationResult:
    state: InvestigationState
    registry: EvidenceRegistry
    report: InvestigationReport | None
    rounds: list[ReportRound] = field(default_factory=list)
    conversation: list[Any] = field(default_factory=list)

    @property
    def first_round(self) -> ReportRound | None:
        return self.rounds[0] if self.rounds else None


def _now() -> datetime:
    return datetime.now(UTC)


class Investigator:
    def __init__(self, provider: ModelProvider, client: OpsClient, *, trace: TraceStore | None = None,
                 budget: InvestigationBudget | None = None, allowed_tools: set[str] | None = None,
                 config: GenerationConfig | None = None, provider_retries: int = 1) -> None:
        self.provider = provider
        self.client = client
        self.trace = trace or TraceStore(":memory:")
        self.budget = budget or InvestigationBudget()
        self.allowed_tools = allowed_tools
        self.config = config or GenerationConfig()
        self.provider_retries = provider_retries
        self.system = SystemPrompt(text=SYSTEM_PROMPT)

    # ------------------------------------------------------------------ public
    async def run(self, incident_id: str, *, investigation_id: str | None = None) -> InvestigationResult:
        investigation_id = investigation_id or f"inv-{incident_id.lower()}-{uuid.uuid4().hex[:8]}"
        state = InvestigationState(investigation_id=investigation_id, incident_id=incident_id, budget=self.budget)
        registry = EvidenceRegistry(investigation_id)
        validator = GroundingValidator(registry)
        result = InvestigationResult(state=state, registry=registry, report=None)
        started = perf_counter()
        self.trace.start_investigation(investigation_id=investigation_id, incident_id=incident_id,
                                       provider=self.provider.info.model_dump(), transport=self.client.transport,
                                       budget=self.budget.model_dump())
        conversation: Conversation = []
        result.conversation = conversation

        def elapsed() -> float:
            return perf_counter() - started

        def move(to: InvestigationStatus, note: str = "") -> None:
            transition(state, to, note)
            self.trace.record_status_change(investigation_id, state.history[-1].model_dump(mode="json"))

        try:
            # ---------------------------------------------------------- seeding
            move(S.SEEDING, "discover tools; read incident queue and topology through MCP")
            tool_specs: dict[str, ToolSpec] = {}
            for descriptor in await self.client.list_tools():
                tool_specs[descriptor.name] = ToolSpec(name=descriptor.name, description=descriptor.description,
                                                       input_schema=descriptor.input_schema)
            for spec in local_action_specs():
                tool_specs[spec.name] = spec
            policy = ActionPolicy(tool_specs, allowed_tools=self.allowed_tools,
                                  max_actions_per_step=self.budget.max_actions_per_step)

            queue = await self.client.read_as_evidence(registry, "incidents://open")
            self._record_evidence(investigation_id, queue)
            state.seen_calls[resource_key("incidents://open")] = queue.evidence_id
            if not queue.ok:
                raise InvestigationError(f"could not read the incident queue: {queue.error}")
            raw = next((i for i in (queue.payload or {}).get("incidents", []) if i.get("id") == incident_id), None)
            if raw is None:
                raise InvestigationError(f"incident {incident_id} is not in the open incident queue")
            incident = IncidentBrief.model_validate(raw)
            state.incident = incident
            self.trace.record_incident(investigation_id, incident.model_dump(mode="json"))
            topology = await self.client.read_as_evidence(registry, f"topology://services/{incident.affected_service}")
            self._record_evidence(investigation_id, topology)
            state.seen_calls[resource_key(topology.source_name)] = topology.evidence_id
            conversation.append(UserMessage(text=render_seed(incident, [queue, topology],
                                                             self.budget.max_evidence_chars_per_result)))
            move(S.DELIBERATING, "seeded")

            # ---------------------------------------------------------- deliberation loop
            while True:
                state.usage.elapsed_seconds = elapsed()
                exhausted = deliberation_exhausted(self.budget, state.usage)
                if exhausted:
                    state.termination_reason = "; ".join(exhausted)
                    break
                step_no = state.usage.steps + 1
                state.usage.steps = step_no
                step = StepRecord(step=step_no, started_at=_now())
                state.steps.append(step)
                context = self._context(state, incident, "deliberate")
                turn, call_id = await self._model_call(state, conversation, purpose="deliberate",
                                                       tools=list(tool_specs.values()), context=context)
                assert isinstance(turn, ModelTurn)
                step.model_call_id = call_id
                step.assistant_text = turn.text
                conversation.append(AssistantMessage(text=turn.text, tool_requests=turn.tool_requests, opaque=turn.opaque))
                if not turn.tool_requests:
                    state.finish_requested = True
                    state.finish_reason = "provider returned no further actions"
                    step.finish_requested = True
                    step.ended_at = _now()
                    self.trace.record_step(investigation_id, step.model_dump(mode="json"))
                    break
                move(S.GATHERING, f"step {step_no}: {len(turn.tool_requests)} requested action(s)")
                remaining = state.usage.remaining(self.budget)
                decisions = policy.review(turn.tool_requests, seen_calls=state.seen_calls,
                                          remaining_tool_calls=remaining["tool_calls"],
                                          remaining_resource_reads=remaining["resource_reads"])
                results: list[ToolResult] = []
                for decision in decisions:
                    outcome, tool_result = await self._execute(decision, state, registry, step_no)
                    step.actions.append(outcome)
                    self.trace.record_action(investigation_id, step_no, outcome.model_dump(mode="json"))
                    results.append(tool_result)
                conversation.append(ToolResultsMessage(results=results))
                step.finish_requested = state.finish_requested
                step.ended_at = _now()
                self.trace.record_step(investigation_id, step.model_dump(mode="json"))
                if state.finish_requested:
                    break
                move(S.DELIBERATING, f"step {step_no} gathered")

            # ---------------------------------------------------------- concluding
            move(S.CONCLUDING, state.finish_reason or state.termination_reason or "")
            conversation.append(UserMessage(text=render_report_request(
                f"Budget remaining: {state.usage.remaining(self.budget)}.")))
            round_ = await self._report_round(state, conversation, incident, registry, validator, 0)
            result.rounds.append(round_)
            move(S.VALIDATING, f"draft 0: {len(round_.validation.errors)} error(s)")
            while (not round_.validation.ok and state.usage.repair_rounds < self.budget.max_repair_rounds
                   and state.usage.model_calls < self.budget.max_model_calls):
                round_index = state.usage.repair_rounds + 1
                move(S.REPAIRING, f"repair round {round_index}")
                if round_.draft is None:
                    feedback = (f"The report could not be parsed ({round_.parse_error}). "
                                f"Return a report that matches the schema exactly.")
                else:
                    feedback = render_validation_failure(round_.validation, round_index)
                self.trace.record_repair(investigation_id, round_index, feedback)
                conversation.append(UserMessage(text=feedback))
                state.usage.repair_rounds = round_index
                round_ = await self._report_round(state, conversation, incident, registry, validator, round_index)
                result.rounds.append(round_)
                move(S.VALIDATING, f"draft {round_index}: {len(round_.validation.errors)} error(s)")

            # ---------------------------------------------------------- terminal
            if round_.draft is None or not round_.validation.ok:
                final = S.FAILED_VALIDATION
            elif round_.validation.warnings:
                final = S.COMPLETED_WITH_WARNINGS
            else:
                final = S.COMPLETED
            state.usage.elapsed_seconds = elapsed()
            if round_.draft is not None:
                result.report = self._build_report(round_.draft, round_.validation, state, incident, registry, final)
                self.trace.record_report(investigation_id, result.report.model_dump(mode="json"))
            move(final, "validated" if final is not S.FAILED_VALIDATION else "validation errors remain after repairs")
        except Exception as exc:
            state.error = f"{exc.__class__.__name__}: {exc}"
            if state.status not in TERMINAL_STATUSES:
                move(S.FAILED, state.error)
        finally:
            state.usage.elapsed_seconds = round(elapsed(), 3)
            self.trace.finish_investigation(investigation_id, status=state.status.value,
                                            termination_reason=state.termination_reason,
                                            usage=state.usage.model_dump(), error=state.error)
        return result

    # ------------------------------------------------------------------ internals
    def _context(self, state: InvestigationState, incident: IncidentBrief, purpose: str) -> InvestigationContext:
        return InvestigationContext(
            investigation_id=state.investigation_id, incident_id=state.incident_id,
            affected_service=incident.affected_service, investigation_clock=incident.investigation_clock,
            step=state.usage.steps, purpose=purpose, budget_remaining=state.usage.remaining(self.budget),  # type: ignore[arg-type]
        )

    def _record_evidence(self, investigation_id: str, item: EvidenceItem) -> None:
        self.trace.record_evidence(investigation_id, item.model_dump(mode="json"))

    async def _model_call(self, state: InvestigationState, conversation: Conversation, *, purpose: str,
                          tools: list[ToolSpec], context: InvestigationContext,
                          schema: type[BaseModel] | None = None) -> tuple[ModelTurn | StructuredResult, str]:
        attempts = 0
        while True:
            if state.usage.model_calls >= self.budget.max_model_calls:
                raise InvestigationError(f"model-call budget ({self.budget.max_model_calls}) exhausted before {purpose}")
            attempts += 1
            state.usage.model_calls += 1
            call_id = f"{state.investigation_id}-mc{state.usage.model_calls:03d}"
            fingerprint = request_fingerprint(self.system, conversation, tools, schema.__name__ if schema else None)
            started = perf_counter()
            try:
                with anyio.fail_after(self.config.timeout_seconds):
                    if schema is None:
                        out: ModelTurn | StructuredResult = await self.provider.complete(
                            self.system, list(conversation), tools=tools, context=context, config=self.config)
                    else:
                        out = await self.provider.generate_structured(
                            self.system, list(conversation), schema=schema, context=context, config=self.config)
            except (ProviderError, TimeoutError) as exc:
                latency = (perf_counter() - started) * 1000
                self.trace.record_model_call(state.investigation_id, {
                    "id": call_id, "step": state.usage.steps, "purpose": purpose, "provider_name": self.provider.info.name,
                    "request_fingerprint": fingerprint, "tool_count": len(tools), "latency_ms": round(latency, 3),
                    "error": f"{exc.__class__.__name__}: {exc}",
                })
                if attempts > self.provider_retries:
                    raise InvestigationError(
                        f"provider failed after {attempts} attempt(s): {exc.__class__.__name__}: {exc}") from exc
                continue
            self.trace.record_model_call(state.investigation_id, {
                "id": call_id, "step": state.usage.steps, "purpose": purpose, "provider_name": self.provider.info.name,
                "request_fingerprint": fingerprint, "tool_count": len(tools), "latency_ms": out.latency_ms,
                "input_tokens": out.usage.input_tokens, "output_tokens": out.usage.output_tokens,
                "stop_reason": getattr(out, "stop_reason", "structured"), "response": out.model_dump(mode="json"),
            })
            return out, call_id

    async def _execute(self, decision: PolicyDecision, state: InvestigationState, registry: EvidenceRegistry,
                       step_no: int) -> tuple[ActionOutcome, ToolResult]:
        if decision.rejection is not None:
            rej = decision.rejection
            if rej.code == "duplicate_call":
                state.usage.suppressed_duplicates += 1
            else:
                state.usage.rejected_actions += 1
            outcome = ActionOutcome(request_id=rej.request_id, kind="rejected", name=rej.name, accepted=False,
                                    rejection_code=rej.code, rejection_reason=rej.reason, duplicate_of=decision.duplicate_of)
            hint = f" Reuse {decision.duplicate_of}." if decision.duplicate_of else ""
            return outcome, ToolResult(request_id=rej.request_id, content=render_rejection(rej.code, rej.reason) + hint,
                                       is_error=True)

        action = decision.action
        max_chars = self.budget.max_evidence_chars_per_result
        if isinstance(action, CallTool):
            item = await self.client.gather(registry, action.name, action.arguments)
            state.usage.tool_calls += 1
            state.seen_calls[call_key(action.name, action.arguments)] = item.evidence_id
            self._record_evidence(state.investigation_id, item)
            outcome = ActionOutcome(request_id=action.request_id, kind="call_tool", name=action.name,
                                    arguments=action.arguments, accepted=True, evidence_id=item.evidence_id, ok=item.ok,
                                    error=item.error, latency_ms=item.latency_ms)
            return outcome, ToolResult(request_id=action.request_id, content=render_evidence(item, max_chars), is_error=not item.ok)

        if isinstance(action, ReadResource):
            item = await self.client.read_as_evidence(registry, action.uri)
            state.usage.resource_reads += 1
            state.seen_calls[resource_key(action.uri)] = item.evidence_id
            self._record_evidence(state.investigation_id, item)
            outcome = ActionOutcome(request_id=action.request_id, kind="read_resource", name="read_resource",
                                    arguments={"uri": action.uri}, accepted=True, evidence_id=item.evidence_id, ok=item.ok,
                                    error=item.error, latency_ms=item.latency_ms)
            return outcome, ToolResult(request_id=action.request_id, content=render_evidence(item, max_chars), is_error=not item.ok)

        if isinstance(action, UpdateHypotheses):
            check = CitationValidator(registry).check
            assigned, errors = state.hypotheses.apply_all(action.updates, step=step_no, check=check)
            for hypothesis_id in assigned.values():
                hypothesis = state.hypotheses.get(hypothesis_id)
                if hypothesis is not None:
                    self.trace.record_hypothesis(state.investigation_id, step_no, hypothesis.model_dump(mode="json"),
                                                 note=hypothesis.revisions[-1].note if hypothesis.revisions else "")
            outcome = ActionOutcome(request_id=action.request_id, kind="update_hypotheses", name="update_hypotheses",
                                    arguments={"updates": [u.model_dump(mode="json") for u in action.updates]}, accepted=True,
                                    ok=not errors, error="; ".join(errors) if errors else None)
            return outcome, ToolResult(request_id=action.request_id, content=render_hypotheses_result(assigned, errors),
                                       is_error=bool(errors))

        assert isinstance(action, FinishInvestigation)
        state.finish_requested = True
        state.finish_reason = action.reason or "provider declared the evidence sufficient"
        outcome = ActionOutcome(request_id=action.request_id, kind="finish_investigation", name="finish_investigation",
                                arguments={"reason": action.reason}, accepted=True, ok=True)
        return outcome, ToolResult(request_id=action.request_id,
                                   content="acknowledged: the structured report will be requested next")

    async def _report_round(self, state: InvestigationState, conversation: Conversation, incident: IncidentBrief,
                            registry: EvidenceRegistry, validator: GroundingValidator, round_index: int) -> ReportRound:
        purpose = "report" if round_index == 0 else "repair"
        context = self._context(state, incident, purpose)
        structured, _ = await self._model_call(state, conversation, purpose=purpose, tools=[], context=context,
                                               schema=ReportDraft)
        assert isinstance(structured, StructuredResult)
        if structured.value is None or not isinstance(structured.value, ReportDraft):
            parse_error = structured.parse_error or "provider returned no report"
            validation = ValidationResult(issues=[ValidationIssue(rule="G0", severity="error", path="<root>",
                                                                  message=f"report did not parse: {parse_error}")])
            self.trace.record_validation(state.investigation_id, round_index, ok=False,
                                         issues=[i.model_dump() for i in validation.issues], draft=structured.raw,
                                         parse_error=parse_error)
            return ReportRound(round_index, None, validation, parse_error)
        draft = structured.value
        validation = validator.validate(draft, investigation_id=state.investigation_id)
        conversation.append(AssistantMessage(text=draft.model_dump_json()))
        self.trace.record_validation(state.investigation_id, round_index, ok=validation.ok,
                                     issues=[i.model_dump() for i in validation.issues],
                                     draft=draft.model_dump(mode="json"), parse_error=None)
        return ReportRound(round_index, draft, validation)

    def _build_report(self, draft: ReportDraft, validation: ValidationResult, state: InvestigationState,
                      incident: IncidentBrief, registry: EvidenceRegistry, final: InvestigationStatus) -> InvestigationReport:
        cited: set[str] = set()
        for claim in [*draft.key_findings, *draft.contradicting_evidence, *draft.unknowns]:
            cited.update(_base(c) for c in claim.evidence_ids)
        for hyp in draft.hypotheses_considered:
            cited.update(_base(c) for c in [*hyp.supporting_evidence_ids, *hyp.contradicting_evidence_ids])
        for action in draft.recommended_actions:
            cited.update(_base(c) for c in action.evidence_ids)
        if draft.primary_hypothesis is not None:
            cited.update(_base(c) for c in draft.primary_hypothesis.supporting_evidence_ids)
        index = [EvidenceSummary(evidence_id=i.evidence_id, source_kind=i.source_kind, source_name=i.source_name,
                                 result_kind=i.result_kind, record_count=len(i.source_ids), ok=i.ok,
                                 cited=i.evidence_id in cited) for i in registry.items()]
        usage = state.usage
        return InvestigationReport(
            **draft.model_dump(), investigation_id=state.investigation_id, incident_id=state.incident_id,
            affected_service=incident.affected_service, provider=self.provider.info, terminal_status=final.value,
            validation=validation, repair_rounds=usage.repair_rounds, steps_used=usage.steps, tool_calls_used=usage.tool_calls,
            resource_reads_used=usage.resource_reads, model_calls_used=usage.model_calls,
            suppressed_duplicate_calls=usage.suppressed_duplicates, rejected_actions=usage.rejected_actions,
            investigation_duration_seconds=round(usage.elapsed_seconds, 3), budget_exhausted=bool(state.termination_reason),
            termination_reason=state.termination_reason, evidence_index=index,
        )


def _base(citation: str) -> str:
    parsed = parse_citation(citation)
    return parsed.evidence_id if parsed else citation
