"""Citation grammar and validation.

    EVD-000004              cite a whole evidence item
    EVD-000004#DEP-0083     cite one world record inside that item

A citation is valid only if the evidence item exists in *this* investigation's
registry and, when narrowed, the record ID is among that item's ``source_ids``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from signalforge.evidence.models import EvidenceItem
from signalforge.evidence.registry import EvidenceRegistry

CITATION_PATTERN = re.compile(r"^(EVD-\d{6})(?:#([A-Za-z0-9][A-Za-z0-9._:/-]{0,199}))?$")
_INLINE = re.compile(r"EVD-\d{6}(?:#[A-Za-z0-9][A-Za-z0-9._:/-]{0,199})?")

CitationCode = Literal[
    "ok", "malformed", "unknown_evidence", "unknown_record", "foreign_investigation", "error_evidence",
]


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    record_id: str | None = None

    def __str__(self) -> str:
        return f"{self.evidence_id}#{self.record_id}" if self.record_id else self.evidence_id


@dataclass(frozen=True)
class CitationCheck:
    raw: str
    citation: Citation | None
    code: CitationCode
    message: str

    @property
    def ok(self) -> bool:
        return self.code in ("ok", "error_evidence")

    @property
    def warning(self) -> bool:
        return self.code == "error_evidence"


def parse_citation(text: str) -> Citation | None:
    match = CITATION_PATTERN.match(text.strip())
    if not match:
        return None
    return Citation(evidence_id=match.group(1), record_id=match.group(2))


def extract_citations(text: str) -> list[str]:
    """All citation-shaped tokens in free text, in order, de-duplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for token in _INLINE.findall(text):
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


class CitationValidator:
    def __init__(self, registry: EvidenceRegistry) -> None:
        self.registry = registry

    def check(self, raw: str) -> CitationCheck:
        citation = parse_citation(raw)
        if citation is None:
            return CitationCheck(raw, None, "malformed", f"{raw!r} is not a citation (expected EVD-000000 or EVD-000000#RECORD)")
        item = self.registry.get(citation.evidence_id)
        if item is None:
            return CitationCheck(raw, citation, "unknown_evidence",
                                 f"{citation.evidence_id} was not gathered in investigation {self.registry.investigation_id}")
        if citation.record_id is not None and not item.has_record(citation.record_id):
            return CitationCheck(raw, citation, "unknown_record",
                                 f"{citation.evidence_id} does not contain record {citation.record_id}")
        if not item.ok:
            return CitationCheck(raw, citation, "error_evidence",
                                 f"{citation.evidence_id} is a failed call ({item.error}); it cannot support a claim")
        return CitationCheck(raw, citation, "ok", "valid")

    def check_many(self, raws: list[str]) -> list[CitationCheck]:
        return [self.check(raw) for raw in raws]

    def check_item(self, item: EvidenceItem) -> CitationCheck:
        """Validate an evidence *object* against this registry (catches cross-investigation reuse)."""
        if item.investigation_id != self.registry.investigation_id:
            return CitationCheck(item.evidence_id, Citation(item.evidence_id), "foreign_investigation",
                                 f"{item.evidence_id} belongs to investigation {item.investigation_id}, "
                                 f"not {self.registry.investigation_id}")
        mine = self.registry.get(item.evidence_id)
        if mine is None or mine.content_hash != item.content_hash:
            return CitationCheck(item.evidence_id, Citation(item.evidence_id), "unknown_evidence",
                                 f"{item.evidence_id} is not the item registered in this investigation")
        return CitationCheck(item.evidence_id, Citation(item.evidence_id), "ok", "valid")
