"""Explicit hypothesis tracking with full revision history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from signalforge.evidence.citations import CitationCheck
from signalforge.orchestration.actions import HypothesisUpdate

HypothesisStatus = Literal["proposed", "supported", "weakened", "refuted"]


class HypothesisRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    confidence: float
    status: HypothesisStatus
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    note: str = ""


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^H\d+$")
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: HypothesisStatus
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    created_step: int
    updated_step: int
    revisions: list[HypothesisRevision] = []


class HypothesisBoard(BaseModel):
    """All hypotheses of an investigation, with their evolution preserved."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[Hypothesis] = []
    counter: int = 0

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        return next((h for h in self.hypotheses if h.id == hypothesis_id), None)

    def apply(self, update: HypothesisUpdate, *, step: int,
              check: Callable[[str], CitationCheck]) -> tuple[Hypothesis | None, str | None]:
        """Create or revise one hypothesis. Evidence ids must validate against this investigation's registry."""
        for evidence_id in [*update.supporting_evidence_ids, *update.contradicting_evidence_ids]:
            result = check(evidence_id)
            if not result.ok:
                return None, f"evidence {evidence_id!r} rejected: {result.code} ({result.message})"
        revision = HypothesisRevision(
            step=step, confidence=update.confidence, status=update.status,
            supporting_evidence_ids=list(dict.fromkeys(update.supporting_evidence_ids)),
            contradicting_evidence_ids=list(dict.fromkeys(update.contradicting_evidence_ids)), note=update.note,
        )
        if update.id is not None:
            existing = self.get(update.id)
            if existing is None:
                return None, f"unknown hypothesis id {update.id!r}"
            existing.statement = update.statement
            existing.confidence = update.confidence
            existing.status = update.status
            existing.supporting_evidence_ids = revision.supporting_evidence_ids
            existing.contradicting_evidence_ids = revision.contradicting_evidence_ids
            existing.updated_step = step
            existing.revisions.append(revision)
            return existing, None
        self.counter += 1
        created = Hypothesis(
            id=f"H{self.counter}", statement=update.statement, confidence=update.confidence, status=update.status,
            supporting_evidence_ids=revision.supporting_evidence_ids,
            contradicting_evidence_ids=revision.contradicting_evidence_ids, created_step=step, updated_step=step,
            revisions=[revision],
        )
        self.hypotheses.append(created)
        return created, None

    def apply_all(self, updates: list[HypothesisUpdate], *, step: int,
                  check: Callable[[str], CitationCheck]) -> tuple[dict[str, str], list[str]]:
        """Apply updates in order. Returns (ref-or-index -> assigned id, errors)."""
        assigned: dict[str, str] = {}
        errors: list[str] = []
        for index, update in enumerate(updates):
            hypothesis, error = self.apply(update, step=step, check=check)
            key = update.ref or update.id or f"#{index}"
            if error:
                errors.append(f"{key}: {error}")
            elif hypothesis is not None:
                assigned[key] = hypothesis.id
        return assigned, errors

    def ranked(self) -> list[Hypothesis]:
        return sorted(self.hypotheses, key=lambda h: (-h.confidence, h.id))
