"""The investigation engine: one bounded loop, typed state, real MCP calls, grounded output.

    created -> seeding -> (deliberating <-> gathering)* -> concluding -> validating (-> repairing -> validating)*
            -> completed | completed_with_warnings | failed_validation | failed

The engine is provider-agnostic: it speaks the neutral boundary in
``signalforge.providers.base`` and reacts to normalised ``ProviderFailure``
categories (retryable vs. not). It never inspects vendor objects, never stores
provider-native opaque blocks, and never calls a tool the policy did not accept.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import anyio
from pydantic import BaseModel

from signalforge.audit.store import TraceStore
from signalforge.events import EventType, NullEmitter
from signalforge.events import models as ev
from signalforge.events.store import Emitter, preview
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
    render_status_block,
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
from signalforge.providers.errors import ProviderFailure
from signalforge.reports.schema import (
    EvidenceSummary,
    InvestigationReport,
    ReportDraft,
    TokenUsageSummary,
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


def _evidence_line(item: EvidenceItem) -> str:
    summary = (item.payload or {}).get("summary") if item.payload else None
    detail = summary if isinstance(summary, str) else f"{len(item.source_ids)} record(s)"
    status = "" if item.ok else " [FAILED]"
    return f"{item.evidence_id} {item.source_kind} {item.source_name} ({item.result_kind or 'error'}){status}: {detail[:140]}"


class Investigator:
    def __init__(self, provider: ModelProvider, client: OpsClient, *, trace: TraceStore | None = None,
                 budget: InvestigationBudget | None = None, allowed_tools: set[str] | None = None,
                 config: GenerationConfig | None = None, provider_retries: int = 1,
                 events: Emitter | None = None) -> None:
        self.provider = provider
        self.client = client
        self.trace = trace or TraceStore(":memory:")
        self.budget = budget or InvestigationBudget()
        self.allowed_tools = allowed_tools
        self.config = config or GenerationConfig()
        self.provider_retries = provider_retries
        # Application events are optional: the CLI and the evaluation harness pass nothing and emit nothing.
        self.events: Emitter = events or NullEmitter()
        self.system = SystemPrompt(text=SYSTEM_PROMPT)

    # ------------------------------------------------------------------ public
    async def run(self, incident_id: str, *, investigation_id: str | None = None,
                  on_state: Callable[[InvestigationState], None] | None = None) -> InvestigationResult:
        """Run one bounded investigation.

        ``on_state`` is handed the live state object once, immediately after it is created, so a caller
        (the API) can report progress while the run continues. It is never called again and must not
        mutate the state.
        """
        investigation_id = investigation_id or f"inv-{incident_id.lower()}-{uuid.uuid4().hex[:8]}"
        state = InvestigationState(investigation_id=investigation_id, incident_id=incident_id, budget=self.budget)
        if on_state is not None:
            on_state(state)
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
            previous = state.status
            transition(state, to, note)
            self.trace.record_status_change(investigation_id, state.history[-1].model_dump(mode="json"))
            self.events.emit(EventType.STATUS_CHANGED,
                             ev.StatusChanged(from_status=previous.value, to_status=to.value, note=note))

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
            self._record_evidence(state, queue)
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
            self._record_evidence(state, topology)
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
                self.events.emit(EventType.STEP_STARTED,
                                 ev.StepStarted(step=step_no, budget_remaining=state.usage.remaining(self.budget)))
                conversation.append(UserMessage(text=render_status_block(state, state.usage.remaining(self.budget))))
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
                self.events.emit(EventType.REPAIR_STARTED, ev.RepairStarted(
                    round=round_index, reason=round_.parse_error or f"{len(round_.validation.errors)} grounding error(s)"))
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
                self.events.emit(EventType.REPORT_COMPLETED, ev.ReportCompleted(
                    status=result.report.status, confidence=result.report.confidence,
                    primary_hypothesis_id=result.report.primary_hypothesis.id if result.report.primary_hypothesis else None,
                    validation_ok=round_.validation.ok, repair_rounds=state.usage.repair_rounds))
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
            self._emit_terminal(state, result)
        return result

    def _emit_terminal(self, state: InvestigationState, result: InvestigationResult) -> None:
        """Exactly one terminal event per investigation, whatever went wrong.

        ``investigation.failed`` means the engine could not finish (provider failure, transport, bug).
        A report that exists but failed grounding validation is still a completed pipeline: it reports
        ``investigation.completed`` with ``terminal_status='failed_validation'``.
        """
        if state.status is S.FAILED:
            message, _, _ = (state.error or "investigation failed").partition("\n")
            self.events.emit(EventType.INVESTIGATION_FAILED, ev.InvestigationFailed(
                terminal_status=state.status.value, message=message,
                category=_failure_category(state.error), duration_seconds=state.usage.elapsed_seconds))
            return
        self.events.emit(EventType.INVESTIGATION_COMPLETED, ev.InvestigationCompleted(
            terminal_status=state.status.value, steps=state.usage.steps, tool_calls=state.usage.tool_calls,
            resource_reads=state.usage.resource_reads, evidence_count=len(result.registry),
            rejected_actions=state.usage.rejected_actions + state.usage.suppressed_duplicates,
            duration_seconds=state.usage.elapsed_seconds, termination_reason=state.termination_reason,
            has_report=result.report is not None))

    # ------------------------------------------------------------------ internals
    def _context(self, state: InvestigationState, incident: IncidentBrief, purpose: str) -> InvestigationContext:
        return InvestigationContext(
            investigation_id=state.investigation_id, incident_id=state.incident_id,
            affected_service=incident.affected_service, investigation_clock=incident.investigation_clock,
            step=state.usage.steps, purpose=purpose, budget_remaining=state.usage.remaining(self.budget),  # type: ignore[arg-type]
        )

    def _record_evidence(self, state: InvestigationState, item: EvidenceItem) -> None:
        self.trace.record_evidence(state.investigation_id, item.model_dump(mode="json"))
        state.evidence_index.append(_evidence_line(item))
        self.events.emit(EventType.EVIDENCE_REGISTERED, ev.EvidenceRegistered(
            evidence_id=item.evidence_id, sequence=item.sequence, source_kind=item.source_kind,
            source_name=item.source_name, result_kind=item.result_kind, record_count=len(item.source_ids),
            ok=item.ok))

    async def _model_call(self, state: InvestigationState, conversation: Conversation, *, purpose: str,
                          tools: list[ToolSpec], context: InvestigationContext,
                          schema: type[BaseModel] | None = None) -> tuple[ModelTurn | StructuredResult, str]:
        attempts = 0
        info = self.provider.info
        while True:
            if state.usage.model_calls >= self.budget.max_model_calls:
                raise InvestigationError(f"model-call budget ({self.budget.max_model_calls}) exhausted before {purpose}")
            attempts += 1
            state.usage.model_calls += 1
            call_id = f"{state.investigation_id}-mc{state.usage.model_calls:03d}"
            fingerprint = request_fingerprint(self.system, conversation, tools, schema.__name__ if schema else None)
            base_record = {"id": call_id, "step": state.usage.steps, "purpose": purpose, "provider_name": info.name,
                           "provider_model": info.model, "request_fingerprint": fingerprint, "tool_count": len(tools)}
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
                latency = round((perf_counter() - started) * 1000, 3)
                category = exc.category if isinstance(exc, ProviderFailure) else (
                    "timeout" if isinstance(exc, TimeoutError) else "unknown")
                retryable = exc.retryable if isinstance(exc, ProviderFailure) else True
                self.trace.record_model_call(state.investigation_id, {
                    **base_record, "latency_ms": latency, "error": f"{exc.__class__.__name__}: {exc}",
                    "error_category": category,
                })
                if not retryable or attempts > self.provider_retries:
                    raise InvestigationError(
                        f"provider failed after {attempts} attempt(s) [{category}]: {exc.__class__.__name__}: {exc}") from exc
                continue
            state.usage.add_usage(out.usage)
            usage = out.usage
            self.events.emit(EventType.PROVIDER_COMPLETED, ev.ProviderCompleted(
                step=state.usage.steps, purpose=purpose, provider=info.name, model=info.model,
                stop_reason=getattr(out, "stop_reason", "structured"), latency_ms=out.latency_ms,
                text_preview=preview(getattr(out, "text", None)),
                tool_requests=len(getattr(out, "tool_requests", []) or []), usage_reported=usage.reported,
                input_tokens=usage.input_tokens if usage.reported else None,
                output_tokens=usage.output_tokens if usage.reported else None))
            self.trace.record_model_call(state.investigation_id, {
                **base_record, "latency_ms": out.latency_ms, "usage_reported": usage.reported,
                "input_tokens": usage.input_tokens if usage.reported else None,
                "output_tokens": usage.output_tokens if usage.reported else None,
                "cached_input_tokens": usage.cached_input_tokens if usage.reported else None,
                "reasoning_output_tokens": usage.reasoning_output_tokens if usage.reported else None,
                "stop_reason": getattr(out, "stop_reason", "structured"),
                # Opaque provider blocks (thinking / reasoning items) are never persisted.
                "response": out.model_dump(mode="json", exclude={"opaque"}),
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
            self.events.emit(EventType.TOOL_REJECTED, ev.ToolRejected(
                step=step_no, request_id=rej.request_id, name=rej.name, code=rej.code, reason=rej.reason,
                duplicate_of=decision.duplicate_of))
            hint = f" Reuse {decision.duplicate_of}." if decision.duplicate_of else ""
            return outcome, ToolResult(request_id=rej.request_id, content=render_rejection(rej.code, rej.reason) + hint,
                                       is_error=True)

        action = decision.action
        max_chars = self.budget.max_evidence_chars_per_result
        self.events.emit(EventType.TOOL_REQUESTED, ev.ToolRequested(
            step=step_no, request_id=action.request_id, name=decision.name,
            arguments=action.arguments if isinstance(action, CallTool) else _local_arguments(action)))
        if isinstance(action, CallTool):
            item = await self.client.gather(registry, action.name, action.arguments)
            state.usage.tool_calls += 1
            state.seen_calls[call_key(action.name, action.arguments)] = item.evidence_id
            self._record_evidence(state, item)
            outcome = ActionOutcome(request_id=action.request_id, kind="call_tool", name=action.name,
                                    arguments=action.arguments, accepted=True, evidence_id=item.evidence_id, ok=item.ok,
                                    error=item.error, latency_ms=item.latency_ms)
            self.events.emit(EventType.TOOL_COMPLETED, ev.ToolCompleted(
                step=step_no, request_id=action.request_id, name=action.name, evidence_id=item.evidence_id,
                ok=item.ok, error=item.error, latency_ms=item.latency_ms))
            return outcome, ToolResult(request_id=action.request_id, content=render_evidence(item, max_chars), is_error=not item.ok)

        if isinstance(action, ReadResource):
            item = await self.client.read_as_evidence(registry, action.uri)
            state.usage.resource_reads += 1
            state.seen_calls[resource_key(action.uri)] = item.evidence_id
            self._record_evidence(state, item)
            outcome = ActionOutcome(request_id=action.request_id, kind="read_resource", name="read_resource",
                                    arguments={"uri": action.uri}, accepted=True, evidence_id=item.evidence_id, ok=item.ok,
                                    error=item.error, latency_ms=item.latency_ms)
            self.events.emit(EventType.RESOURCE_READ, ev.ResourceRead(
                step=step_no, request_id=action.request_id, uri=action.uri, evidence_id=item.evidence_id,
                ok=item.ok, error=item.error))
            return outcome, ToolResult(request_id=action.request_id, content=render_evidence(item, max_chars), is_error=not item.ok)

        if isinstance(action, UpdateHypotheses):
            check = CitationValidator(registry).check
            assigned, errors = state.hypotheses.apply_all(action.updates, step=step_no, check=check)
            for hypothesis_id in assigned.values():
                hypothesis = state.hypotheses.get(hypothesis_id)
                if hypothesis is not None:
                    note = hypothesis.revisions[-1].note if hypothesis.revisions else ""
                    self.trace.record_hypothesis(state.investigation_id, step_no, hypothesis.model_dump(mode="json"),
                                                 note=note)
                    self.events.emit(EventType.HYPOTHESIS_UPDATED, ev.HypothesisUpdated(
                        hypothesis_id=hypothesis.id, step=step_no, statement=hypothesis.statement,
                        status=hypothesis.status, confidence=hypothesis.confidence,
                        supporting_evidence_ids=list(hypothesis.supporting_evidence_ids),
                        contradicting_evidence_ids=list(hypothesis.contradicting_evidence_ids), note=note))
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
        self.events.emit(EventType.VALIDATION_STARTED, ev.ValidationStarted(round=round_index))
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
            self.events.emit(EventType.VALIDATION_FAILED, ev.ValidationFailed(
                round=round_index, errors=len(validation.errors), warnings=0, rules=["G0"]))
            return ReportRound(round_index, None, validation, parse_error)
        draft = structured.value
        validation = validator.validate(draft, investigation_id=state.investigation_id)
        conversation.append(AssistantMessage(text=draft.model_dump_json()))
        self.trace.record_validation(state.investigation_id, round_index, ok=validation.ok,
                                     issues=[i.model_dump() for i in validation.issues],
                                     draft=draft.model_dump(mode="json"), parse_error=None)
        if not validation.ok:
            self.events.emit(EventType.VALIDATION_FAILED, ev.ValidationFailed(
                round=round_index, errors=len(validation.errors), warnings=len(validation.warnings),
                rules=sorted({i.rule for i in validation.errors})))
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
            token_usage=TokenUsageSummary(reported=usage.tokens_reported, input_tokens=usage.input_tokens,
                                          output_tokens=usage.output_tokens, cached_input_tokens=usage.cached_input_tokens,
                                          cache_write_tokens=usage.cache_write_tokens,
                                          reasoning_output_tokens=usage.reasoning_output_tokens, model_calls=usage.model_calls),
        )


def _local_arguments(action: Any) -> dict[str, Any]:
    """Safe argument view for the three local (non-MCP) actions."""
    if isinstance(action, ReadResource):
        return {"uri": action.uri}
    if isinstance(action, UpdateHypotheses):
        return {"updates": len(action.updates)}
    return {"reason": getattr(action, "reason", None)}


def _failure_category(error: str | None) -> str | None:
    """Pull the normalised provider category out of an engine error, e.g. '... [rate_limited]: ...'."""
    if not error or "[" not in error or "]" not in error:
        return None
    inner = error.split("[", 1)[1].split("]", 1)[0]
    return inner or None


def _base(citation: str) -> str:
    parsed = parse_citation(citation)
    return parsed.evidence_id if parsed else citation
