"""Query sanitisation: free text in, a safe FTS5 MATCH expression out.

Model- or user-supplied text never reaches SQLite as query syntax. Each term
is a quoted string literal, joined with OR; FTS5 operators, column filters and
punctuation are stripped.
"""

from __future__ import annotations

import re

# Word characters plus '_', '.', '-' so identifiers like latency_p95, reservation.batch_mode and orders-db survive.
# ':' and '/' are deliberately excluded: 'col:term' is FTS5 column-filter syntax and must be split, never passed.
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{1,39}")
_RESERVED = {"and", "or", "not", "near"}
_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "is", "for", "on", "with", "at", "by", "from", "this", "that",
    "it", "as", "be", "are", "was", "were", "why", "what", "how", "does", "do", "did", "our", "we",
}
MAX_TERMS = 16


def query_terms(text: str, max_terms: int = MAX_TERMS) -> list[str]:
    """Normalised, de-duplicated search terms in order of appearance."""
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        term = raw.strip("._-")
        if len(term) < 2 or term in _RESERVED or term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def sanitize_query(text: str, max_terms: int = MAX_TERMS) -> str | None:
    """Return an FTS5 expression of quoted literals joined by OR, or None if nothing searchable."""
    terms = query_terms(text, max_terms)
    if not terms:
        return None
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
