"""Hypothesis board evolution and the grounding validator's structural rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from signalforge.evidence.citations import CitationValidator
from signalforge.evidence.registry import EvidenceRegistry
from signalforge.orchestration.actions import HypothesisUpdate
from signalforge.orchestration.hypotheses import HypothesisBoard
from signalforge.reports.schema import Claim, RecommendedAction, ReportDraft, ReportHypothesis
from signalforge.reports.validation import GroundingValidator


def _registry(investigation_id: str = "inv-A") -> EvidenceRegistry:
    reg = EvidenceRegistry(investigation_id)
    reg.register_resource(uri="incidents://open", text="{}", payload={"kind": "document"}, source_ids=["INC-2026-0101"],
                          ok=True, error=None, latency_ms=1)                                                     # EVD-000001
    reg.register_resource(uri="topology://services/checkout", text="{}", payload=None, source_ids=["checkout", "EDGE-checkout-pricing"],
                          ok=True, error=None, latency_ms=1)                                                     # EVD-000002
    reg.register_tool_result(name="get_deployments", arguments={}, payload={"kind": "deployments", "source_ids": ["DEP-0038"]},
                             text="{}", ok=True, error=None, latency_ms=1)                                       # EVD-000003
    reg.register_tool_result(name="query_metrics", arguments={}, payload={"kind": "metric_series", "source_ids": ["MET-1"]},
                             text="{}", ok=True, error=None, latency_ms=1)                                       # EVD-000004
    reg.register_tool_result(name="query_logs", arguments={"node": "ghost"}, payload=None, text="err", ok=False,
                             error="Unknown node", latency_ms=1)                                                 # EVD-000005 (failed)
    return reg


def _hyp(**kw) -> ReportHypothesis:
    base = dict(id="H1", statement="Release caused latency", category="deployment_regression", status="supported",
                confidence=0.8, supporting_evidence_ids=["EVD-000003#DEP-0038", "EVD-000004"], contradicting_evidence_ids=[])
    base.update(kw)
    return ReportHypothesis(**base)


def _draft(**kw) -> ReportDraft:
    primary = kw.pop("primary", _hyp())
    base = dict(
        summary="A summary of sufficient length for the schema.", status="root_cause_identified", confidence=0.8,
        primary_hypothesis=primary, hypotheses_considered=[primary] if primary else [_hyp(status="refuted", confidence=0.1)],
        key_findings=[Claim(statement="p95 stepped up", kind="OBSERVED", evidence_ids=["EVD-000004"]),
                      Claim(statement="the release explains it", kind="INFERRED", evidence_ids=["EVD-000003", "EVD-000004"])],
        contradicting_evidence=[], recommended_actions=[RecommendedAction(action="Roll back", rationale="onset matches", priority="P1",
                                                                          kind="mitigation", evidence_ids=["EVD-000003"])],
        unknowns=[Claim(statement="whether batch API is ready", kind="UNKNOWN")], limitations=[],
    )
    base.update(kw)
    return ReportDraft(**base)


# ---------------------------------------------------------------- hypotheses


def test_hypothesis_board_creates_updates_and_keeps_history():
    reg = _registry()
    check = CitationValidator(reg).check
    board = HypothesisBoard()
    assigned, errors = board.apply_all([HypothesisUpdate(ref="a", statement="release regression", confidence=0.4),
                                        HypothesisUpdate(ref="b", statement="vendor slow", confidence=0.3,
                                                         supporting_evidence_ids=["EVD-000003#DEP-0038"])], step=1, check=check)
    assert assigned == {"a": "H1", "b": "H2"} and errors == []
    assigned2, errors2 = board.apply_all([HypothesisUpdate(id="H1", statement="release regression confirmed", confidence=0.85,
                                                           status="supported", supporting_evidence_ids=["EVD-000003", "EVD-000004"]),
                                          HypothesisUpdate(id="H2", statement="vendor slow", confidence=0.05, status="refuted",
                                                           contradicting_evidence_ids=["EVD-000004"])], step=2, check=check)
    assert assigned2 == {"H1": "H1", "H2": "H2"} and errors2 == []
    h1 = board.get("H1")
    assert h1.created_step == 1 and h1.updated_step == 2 and [r.confidence for r in h1.revisions] == [0.4, 0.85]
    assert h1.status == "supported" and board.get("H2").status == "refuted"
    assert [h.id for h in board.ranked()] == ["H1", "H2"]


def test_hypothesis_updates_reject_unknown_evidence_and_ids():
    reg = _registry()
    check = CitationValidator(reg).check
    board = HypothesisBoard()
    assigned, errors = board.apply_all([
        HypothesisUpdate(ref="x", statement="ghost evidence", confidence=0.5, supporting_evidence_ids=["EVD-000099"]),
        HypothesisUpdate(ref="y", statement="bad record", confidence=0.5, supporting_evidence_ids=["EVD-000003#DEP-9999"]),
        HypothesisUpdate(ref="z", statement="malformed", confidence=0.5, supporting_evidence_ids=["the deployment"]),
        HypothesisUpdate(id="H7", statement="no such hypothesis", confidence=0.5),
        HypothesisUpdate(ref="ok", statement="fine", confidence=0.5, supporting_evidence_ids=["EVD-000004"]),
    ], step=1, check=check)
    assert assigned == {"ok": "H1"}
    assert len(errors) == 4 and any("unknown_evidence" in e for e in errors) and any("unknown_record" in e for e in errors)
    assert any("malformed" in e for e in errors) and any("unknown hypothesis id" in e for e in errors)
    with pytest.raises(ValidationError):
        HypothesisUpdate(statement="too confident", confidence=1.2)


# ---------------------------------------------------------------- validator


def test_valid_draft_passes_with_only_informational_notes():
    result = GroundingValidator(_registry()).validate(_draft(), investigation_id="inv-A")
    assert result.ok and result.warnings == []
    assert [i.rule for i in result.infos] == ["G9"]  # uncited seed evidence is informational


@pytest.mark.parametrize(("draft", "rule", "fragment"), [
    (_draft(key_findings=[Claim(statement="x y z", kind="OBSERVED", evidence_ids=["EVD-000099"])]), "G1", "not gathered"),
    (_draft(key_findings=[Claim(statement="x y z", kind="OBSERVED", evidence_ids=["deployment DEP-0038"])]), "G1", "malformed"),
    (_draft(key_findings=[Claim(statement="x y z", kind="OBSERVED", evidence_ids=["EVD-000003#DEP-9999"])]), "G2", "not inside"),
    (_draft(key_findings=[Claim(statement="x y z", kind="OBSERVED", evidence_ids=[])]), "G3", "no supporting evidence"),
    (_draft(key_findings=[Claim(statement="x y z", kind="INFERRED", evidence_ids=[])]), "G3", "no supporting evidence"),
    (_draft(key_findings=[Claim(statement="x y z", kind="OBSERVED", evidence_ids=["EVD-000005"])]), "G3", "not directly supported"),
    (_draft(unknowns=[Claim(statement="x y z", kind="UNKNOWN", evidence_ids=["EVD-000004"])]), "G3", "must not cite"),
    (_draft(unknowns=[Claim(statement="x y z", kind="OBSERVED", evidence_ids=["EVD-000004"])]), "G3", "must be UNKNOWN"),
    (_draft(primary=None), "G4", "requires a primary"),
    (_draft(primary=_hyp(supporting_evidence_ids=[])), "G4", "no supporting evidence"),
    (_draft(primary=_hyp(supporting_evidence_ids=["EVD-000004"])), "G4", "at least 2 kinds"),
    (_draft(confidence=0.4, primary=_hyp(confidence=0.4)), "G5", "confidence >= 0.6"),
    (_draft(status="inconclusive", confidence=0.7, primary=_hyp(confidence=0.7, status="inconclusive")), "G5", "<= 0.5"),
    (_draft(status="inconclusive", confidence=0.3, primary=_hyp(confidence=0.3, status="inconclusive"), unknowns=[]), "G5", "explicit unknowns"),
    (_draft(status="inconclusive", confidence=0.3, primary=_hyp(confidence=0.3, status="supported")), "G5", "cannot present"),
    (_draft(primary=_hyp(status="refuted")), "G6", "refuted"),
    (_draft(hypotheses_considered=[_hyp(id="H2")]), "G6", "not among hypotheses_considered"),
    (_draft(recommended_actions=[RecommendedAction(action="Roll back now", rationale="because", priority="P1", kind="mitigation")]), "G7", "must cite"),
])
def test_validator_detects_each_structural_violation(draft, rule, fragment):
    result = GroundingValidator(_registry()).validate(draft)
    assert not result.ok
    assert any(i.rule == rule and fragment.lower() in i.message.lower() for i in result.errors), \
        [(i.rule, i.message) for i in result.errors]


def test_validator_warnings_for_error_evidence_and_confidence_mismatch():
    result = GroundingValidator(_registry()).validate(
        _draft(contradicting_evidence=[Claim(statement="failed lookup", kind="INFERRED", evidence_ids=["EVD-000005", "EVD-000004"])],
               confidence=0.65, primary=_hyp(confidence=0.85), hypotheses_considered=[_hyp(confidence=0.85)]))
    assert result.ok
    assert {i.rule for i in result.warnings} == {"G8", "G6"}


def test_validator_rejects_foreign_investigation_and_foreign_registry_ids():
    result = GroundingValidator(_registry("inv-A")).validate(_draft(), investigation_id="inv-B")
    assert any(i.rule == "G11" for i in result.errors)
    other = EvidenceRegistry("inv-B")  # nothing gathered here
    result_b = GroundingValidator(other).validate(_draft(), investigation_id="inv-B")
    assert result_b.errors and all(i.rule in ("G1", "G4", "G3") for i in result_b.errors)
    assert any("not gathered in investigation inv-B" in i.message for i in result_b.errors)
