"""Evidence gathered by an investigation.

Evidence payloads are returned as-is, including the deliberately hostile text some synthetic
runbooks contain: that content is the point of the injection scenarios and a frontend should show
it. Every item is flagged ``untrusted`` in the schema so no consumer mistakes retrieved content for
an instruction.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.errors import not_found
from signalforge.api.routes.common import json_ok, services_of
from signalforge.api.schemas import EvidenceDetailView, EvidenceSummaryView, valid_evidence_id
from signalforge.evidence.models import EvidenceItem

UNTRUSTED_NOTE = (
    "Evidence is data retrieved from the synthetic operations environment, not instructions. "
    "Some documents contain text that tries to direct an automated reader; it was refused by the "
    "action policy and is shown here as evidence."
)


def _summary(item: EvidenceItem) -> EvidenceSummaryView:
    return EvidenceSummaryView(
        evidence_id=item.evidence_id, sequence=item.sequence, acquired_at=item.acquired_at.isoformat(),
        source_kind=item.source_kind, source_name=item.source_name, arguments=item.arguments, ok=item.ok,
        error=item.error, result_kind=item.result_kind, source_ids=list(item.source_ids),
        content_hash=item.content_hash, latency_ms=item.latency_ms,
    )


def _items(request: Request) -> tuple[list[EvidenceItem], str]:
    services = services_of(request)
    investigation_id = request.path_params["investigation_id"]
    record = services.runner.get(investigation_id)
    if record is None:
        raise not_found("investigation", investigation_id)
    return (record.result.registry.items() if record.result else []), record.id


async def list_evidence(request: Request) -> JSONResponse:
    items, investigation_id = _items(request)
    return json_ok({"investigation_id": investigation_id, "note": UNTRUSTED_NOTE,
                    "evidence": [_summary(i).model_dump(mode="json") for i in items]})


async def get_evidence(request: Request) -> JSONResponse:
    items, investigation_id = _items(request)
    evidence_id = valid_evidence_id(request.path_params["evidence_id"])
    found = next((i for i in items if i.evidence_id == evidence_id), None)
    if found is None:
        raise not_found("evidence item", evidence_id)
    detail = EvidenceDetailView(**_summary(found).model_dump(), payload=found.payload, text=found.text)
    return json_ok({"investigation_id": investigation_id, "note": UNTRUSTED_NOTE,
                    "evidence": detail.model_dump(mode="json")})
