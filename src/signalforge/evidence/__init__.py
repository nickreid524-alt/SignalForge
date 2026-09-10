"""Client-side evidence registry: investigation-local EVD IDs over stable world record IDs."""

from signalforge.evidence.citations import (
    CITATION_PATTERN,
    Citation,
    CitationCheck,
    CitationValidator,
    extract_citations,
    parse_citation,
)
from signalforge.evidence.models import EvidenceItem
from signalforge.evidence.registry import EvidenceRegistry, canonical_json, content_hash

__all__ = [
    "CITATION_PATTERN",
    "Citation",
    "CitationCheck",
    "CitationValidator",
    "EvidenceItem",
    "EvidenceRegistry",
    "canonical_json",
    "content_hash",
    "extract_citations",
    "parse_citation",
]
