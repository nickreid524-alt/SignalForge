"""Create investigations, watch them, and read what they produced."""

from __future__ import annotations

from typing import Any

from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.errors import ApiError, not_found
from signalforge.api.routes.common import json_body, json_ok, services_of
from signalforge.api.runner import InvestigationRecord
from signalforge.api.schemas import (
    BudgetUsageView,
    CreateInvestigation,
    HypothesisRevisionView,
    HypothesisView,
    InvestigationCreated,
    InvestigationDetail,
    InvestigationSummary,
    TokenUsage,
    ValidationView,
    parse_body,
    valid_investigation_id,
)
from signalforge.api.services import AppServices
from signalforge.api.streaming import PING_SECONDS, event_stream, last_event_id
from signalforge.api.trace_view import safe_trace


def _links(investigation_id: str) -> dict[str, str]:
    base = f"/api/investigations/{investigation_id}"
    return {"self": base, "events": f"{base}/events", "evidence": f"{base}/evidence",
            "hypotheses": f"{base}/hypotheses", "report": f"{base}/report", "trace": f"{base}/trace"}


def _summary_fields(record: InvestigationRecord) -> dict[str, Any]:
    return {
        "id": record.id, "incident_id": record.incident_id, "status": record.status, "terminal": record.terminal,
        "provider": record.provider_name, "provider_mode": record.provider_mode,
        "uses_live_api": record.uses_live_api, "model": record.model, "budget_profile": record.budget_profile,
        "created_at": record.created_at.isoformat(),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
    }


def _record_or_404(request: Request) -> tuple[AppServices, InvestigationRecord]:
    services = services_of(request)
    investigation_id = valid_investigation_id(request.path_params["investigation_id"])
    record = services.runner.get(investigation_id)
    if record is None:
        raise not_found("investigation", investigation_id)
    return services, record


def _hypotheses(record: InvestigationRecord) -> list[HypothesisView]:
    state = record.state
    if state is None:
        return []
    return [
        HypothesisView(
            id=h.id, statement=h.statement, status=h.status, confidence=h.confidence,
            supporting_evidence_ids=list(h.supporting_evidence_ids),
            contradicting_evidence_ids=list(h.contradicting_evidence_ids),
            created_step=h.created_step, updated_step=h.updated_step,
            revisions=[HypothesisRevisionView(step=r.step, status=r.status, confidence=r.confidence,
                                              supporting_evidence_ids=list(r.supporting_evidence_ids),
                                              contradicting_evidence_ids=list(r.contradicting_evidence_ids),
                                              note=r.note) for r in h.revisions],
        )
        for h in state.hypotheses.hypotheses
    ]


# ---------------------------------------------------------------------- create


async def create_investigation(request: Request) -> JSONResponse:
    services = services_of(request)
    body = parse_body(CreateInvestigation, await json_body(request))
    incident = services.incident(body.incident_id)
    if incident is None:
        raise not_found("incident", body.incident_id)
    record = services.runner.submit(
        body.incident_id, provider_name=body.provider, budget_profile=body.budget_profile,
        incident_title=incident.title, affected_service=incident.affected_service,
    )
    return json_ok(InvestigationCreated(**_summary_fields(record), links=_links(record.id)), status_code=202)


async def list_investigations(request: Request) -> JSONResponse:
    services = services_of(request)
    return json_ok({"investigations": [InvestigationSummary(**_summary_fields(r)).model_dump(mode="json")
                                       for r in services.runner.list()]})


# ---------------------------------------------------------------------- status


async def get_investigation(request: Request) -> JSONResponse:
    services, record = _record_or_404(request)
    state = record.state
    report = record.result.report if record.result else None
    usage = state.usage if state else None
    validation = ValidationView()
    if report is not None:
        validation = ValidationView(ok=report.validation.ok, errors=len(report.validation.errors),
                                    warnings=len(report.validation.warnings), repair_rounds=report.repair_rounds,
                                    issues=[i.model_dump(mode="json") for i in report.validation.issues])
    tokens = TokenUsage(reported=False)
    if usage is not None:
        tokens = TokenUsage(
            reported=usage.tokens_reported,
            input_tokens=usage.input_tokens if usage.tokens_reported else None,
            output_tokens=usage.output_tokens if usage.tokens_reported else None,
            cached_input_tokens=usage.cached_input_tokens if usage.tokens_reported else None,
            reasoning_output_tokens=usage.reasoning_output_tokens if usage.tokens_reported else None,
            model_calls=usage.model_calls)
    detail = InvestigationDetail(
        **_summary_fields(record),
        incident=services.incident(record.incident_id),
        steps=usage.steps if usage else 0,
        tool_calls=usage.tool_calls if usage else 0,
        resource_reads=usage.resource_reads if usage else 0,
        model_calls=usage.model_calls if usage else 0,
        evidence_count=len(record.result.registry) if record.result else 0,
        rejected_actions=usage.rejected_actions if usage else 0,
        suppressed_duplicates=usage.suppressed_duplicates if usage else 0,
        hypotheses=_hypotheses(record),
        validation=validation,
        token_usage=tokens,
        budget=record.budget.model_dump(mode="json"),
        usage=BudgetUsageView(**{k: v for k, v in (usage.model_dump() if usage else {}).items()
                                 if k in BudgetUsageView.model_fields}),
        budget_exhausted=bool(state.termination_reason) if state else False,
        termination_reason=state.termination_reason if state else None,
        report_available=report is not None,
        error=record.error or (state.error if state else None),
        links=_links(record.id),
    )
    return json_ok(detail)


async def get_hypotheses(request: Request) -> JSONResponse:
    _, record = _record_or_404(request)
    return json_ok({"investigation_id": record.id, "status": record.status,
                    "hypotheses": [h.model_dump(mode="json") for h in _hypotheses(record)]})


# ---------------------------------------------------------------------- report and trace


async def get_report(request: Request) -> JSONResponse:
    _, record = _record_or_404(request)
    report = record.result.report if record.result else None
    if report is None:
        raise ApiError("report_not_ready",
                       f"investigation {record.id} has no report yet (status {record.status!r})",
                       details={"status": record.status, "terminal": record.terminal})
    return json_ok(report.model_dump(mode="json"))


async def get_trace(request: Request) -> JSONResponse:
    services, record = _record_or_404(request)
    bundle = services.trace.load(record.id)
    if bundle is None:
        raise not_found("trace", record.id)
    return json_ok(safe_trace(bundle))


# ---------------------------------------------------------------------- events


async def stream_events(request: Request) -> Any:
    services = services_of(request)
    investigation_id = valid_investigation_id(request.path_params["investigation_id"])
    if services.runner.get(investigation_id) is None and services.events.last_seq(investigation_id) == 0:
        raise not_found("investigation", investigation_id)
    after = last_event_id(request.headers, request.query_params)
    return EventSourceResponse(event_stream(services.events, investigation_id, after), ping=PING_SECONDS)
