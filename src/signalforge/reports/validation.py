"""Deterministic grounding validation of a report draft against one investigation's evidence registry.

Structural guarantees only: every citation resolves, every kind of claim cites
what its kind requires, the primary hypothesis is supported, statuses and
confidences are coherent. Semantic support (does EVD-000004 really say that?)
is out of scope here and is measured by the evaluation harness instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from signalforge.evidence.citations import CitationValidator, parse_citation
from signalforge.evidence.registry import EvidenceRegistry
from signalforge.reports.schema import ReportDraft, ValidationIssue, ValidationResult

MIN_CONFIDENCE_IDENTIFIED = 0.6
MAX_CONFIDENCE_INCONCLUSIVE = 0.5
MIN_DISTINCT_KINDS_IDENTIFIED = 2


def _base_id(citation: str) -> str:
    parsed = parse_citation(citation)
    return parsed.evidence_id if parsed else citation


class GroundingValidator:
    def __init__(self, registry: EvidenceRegistry) -> None:
        self.registry = registry
        self.citations = CitationValidator(registry)

    # ------------------------------------------------------------------ helpers
    def _check_citations(self, ids: Iterable[str], path: str, issues: list[ValidationIssue]) -> None:
        for raw in ids:
            check = self.citations.check(raw)
            if check.code == "ok":
                continue
            if check.code == "error_evidence":
                issues.append(ValidationIssue(rule="G8", severity="warning", path=path,
                                              message=f"{raw} is a failed call and cannot support a claim"))
            elif check.code == "malformed":
                issues.append(ValidationIssue(rule="G1", severity="error", path=path,
                                              message=f"malformed citation {raw!r}"))
            elif check.code == "unknown_evidence":
                issues.append(ValidationIssue(rule="G1", severity="error", path=path,
                                              message=f"{raw} was not gathered in investigation {self.registry.investigation_id}"))
            elif check.code == "unknown_record":
                issues.append(ValidationIssue(rule="G2", severity="error", path=path,
                                              message=f"{raw}: record is not inside that evidence item"))
            else:
                issues.append(ValidationIssue(rule="G1", severity="error", path=path, message=check.message))

    def _directly_observed(self, ids: list[str]) -> bool:
        """At least one cited item is a successful tool result or resource read."""
        for raw in ids:
            item = self.registry.get(_base_id(raw))
            if item is not None and item.ok and item.source_kind in ("tool", "resource"):
                return True
        return False

    # ------------------------------------------------------------------ main entry
    def validate(self, draft: ReportDraft, *, investigation_id: str | None = None) -> ValidationResult:
        issues: list[ValidationIssue] = []

        if investigation_id is not None and investigation_id != self.registry.investigation_id:
            issues.append(ValidationIssue(rule="G11", severity="error", path="investigation_id",
                                          message=f"report is for {investigation_id}, registry is {self.registry.investigation_id}"))

        # G1/G2/G8 — every citation anywhere in the report
        for index, claim in enumerate(draft.key_findings):
            self._check_citations(claim.evidence_ids, f"key_findings[{index}]", issues)
        for index, claim in enumerate(draft.contradicting_evidence):
            self._check_citations(claim.evidence_ids, f"contradicting_evidence[{index}]", issues)
        for index, claim in enumerate(draft.unknowns):
            self._check_citations(claim.evidence_ids, f"unknowns[{index}]", issues)
        for index, hyp in enumerate(draft.hypotheses_considered):
            self._check_citations(hyp.supporting_evidence_ids, f"hypotheses_considered[{index}].supporting", issues)
            self._check_citations(hyp.contradicting_evidence_ids, f"hypotheses_considered[{index}].contradicting", issues)
        for index, action in enumerate(draft.recommended_actions):
            self._check_citations(action.evidence_ids, f"recommended_actions[{index}]", issues)
        if draft.primary_hypothesis is not None:
            self._check_citations(draft.primary_hypothesis.supporting_evidence_ids, "primary_hypothesis.supporting", issues)
            self._check_citations(draft.primary_hypothesis.contradicting_evidence_ids, "primary_hypothesis.contradicting", issues)

        # G3 — claim kinds
        for section, claims in (("key_findings", draft.key_findings), ("contradicting_evidence", draft.contradicting_evidence),
                                ("unknowns", draft.unknowns)):
            for index, claim in enumerate(claims):
                path = f"{section}[{index}]"
                if claim.kind == "UNKNOWN":
                    if claim.evidence_ids:
                        issues.append(ValidationIssue(rule="G3", severity="error", path=path,
                                                      message="UNKNOWN claims must not cite evidence"))
                    continue
                if not claim.evidence_ids:
                    issues.append(ValidationIssue(rule="G3", severity="error", path=path,
                                                  message=f"{claim.kind} claim has no supporting evidence"))
                elif claim.kind == "OBSERVED" and not self._directly_observed(claim.evidence_ids):
                    issues.append(ValidationIssue(rule="G3", severity="error", path=path,
                                                  message="OBSERVED claim is not directly supported by successful tool/resource evidence"))
        for index, claim in enumerate(draft.unknowns):
            if claim.kind != "UNKNOWN":
                issues.append(ValidationIssue(rule="G3", severity="error", path=f"unknowns[{index}]",
                                              message="entries in unknowns must be UNKNOWN claims"))

        # G4/G5/G6 — primary hypothesis, status and confidence coherence
        primary = draft.primary_hypothesis
        if draft.status in ("root_cause_identified", "probable_cause"):
            if primary is None:
                issues.append(ValidationIssue(rule="G4", severity="error", path="primary_hypothesis",
                                              message=f"status {draft.status} requires a primary hypothesis"))
            else:
                if not primary.supporting_evidence_ids:
                    issues.append(ValidationIssue(rule="G4", severity="error", path="primary_hypothesis.supporting",
                                                  message="primary hypothesis has no supporting evidence"))
                if primary.status == "refuted":
                    issues.append(ValidationIssue(rule="G6", severity="error", path="primary_hypothesis.status",
                                                  message="a refuted hypothesis cannot be primary"))
                if draft.status == "root_cause_identified":
                    kinds = set()
                    for raw in primary.supporting_evidence_ids:
                        item = self.registry.get(_base_id(raw))
                        if item is not None and item.ok:
                            kinds.add(item.result_kind or item.source_kind)
                    if len(kinds) < MIN_DISTINCT_KINDS_IDENTIFIED:
                        issues.append(ValidationIssue(rule="G4", severity="error", path="primary_hypothesis.supporting",
                                                      message=f"root_cause_identified requires supporting evidence of at least "
                                                              f"{MIN_DISTINCT_KINDS_IDENTIFIED} kinds (have {sorted(kinds)})"))
                    if draft.confidence < MIN_CONFIDENCE_IDENTIFIED:
                        issues.append(ValidationIssue(rule="G5", severity="error", path="confidence",
                                                      message=f"root_cause_identified requires confidence >= {MIN_CONFIDENCE_IDENTIFIED}"))
        if draft.status == "inconclusive":
            if draft.confidence > MAX_CONFIDENCE_INCONCLUSIVE:
                issues.append(ValidationIssue(rule="G5", severity="error", path="confidence",
                                              message=f"inconclusive reports must have confidence <= {MAX_CONFIDENCE_INCONCLUSIVE}"))
            if not draft.unknowns:
                issues.append(ValidationIssue(rule="G5", severity="error", path="unknowns",
                                              message="inconclusive reports must state explicit unknowns"))
            if primary is not None and primary.status == "supported":
                issues.append(ValidationIssue(rule="G5", severity="error", path="primary_hypothesis.status",
                                              message="an inconclusive report cannot present a 'supported' primary hypothesis"))
        if primary is not None:
            if all(h.id != primary.id for h in draft.hypotheses_considered):
                issues.append(ValidationIssue(rule="G6", severity="error", path="primary_hypothesis.id",
                                              message=f"primary hypothesis {primary.id} is not among hypotheses_considered"))
            if abs(primary.confidence - draft.confidence) > 0.1001:
                issues.append(ValidationIssue(rule="G6", severity="warning", path="confidence",
                                              message="report confidence differs from the primary hypothesis by more than 0.1"))

        # G7 — actions
        for index, action in enumerate(draft.recommended_actions):
            if action.kind != "diagnostic" and not action.evidence_ids:
                issues.append(ValidationIssue(rule="G7", severity="error", path=f"recommended_actions[{index}]",
                                              message=f"{action.kind} actions must cite evidence"))

        # G9 — unused evidence (informational)
        cited = {_base_id(c) for c in _all_citations(draft)}
        unused = [i.evidence_id for i in self.registry.items() if i.ok and i.evidence_id not in cited]
        if unused:
            issues.append(ValidationIssue(rule="G9", severity="info", path="evidence",
                                          message=f"{len(unused)} gathered evidence item(s) not cited: {', '.join(unused[:8])}"))
        return ValidationResult(issues=issues)


def _all_citations(draft: ReportDraft) -> list[str]:
    out: list[str] = []
    for claim in [*draft.key_findings, *draft.contradicting_evidence, *draft.unknowns]:
        out.extend(claim.evidence_ids)
    for hyp in draft.hypotheses_considered:
        out.extend(hyp.supporting_evidence_ids)
        out.extend(hyp.contradicting_evidence_ids)
    for action in draft.recommended_actions:
        out.extend(action.evidence_ids)
    if draft.primary_hypothesis is not None:
        out.extend(draft.primary_hypothesis.supporting_evidence_ids)
        out.extend(draft.primary_hypothesis.contradicting_evidence_ids)
    return out
