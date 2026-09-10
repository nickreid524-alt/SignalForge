"""Heading-level chunking of markdown documents with stable chunk identifiers."""

from __future__ import annotations

import re

from signalforge.retrieval.models import Chunk, SourceKind

_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def chunk_markdown(*, source_id: str, source_kind: SourceKind, service: str, title: str, body: str,
                   resource_uri: str) -> list[Chunk]:
    """Split on ``##`` headings. The text before the first heading becomes section "Overview".

    Chunk ids are ``<source_id>#s1``, ``#s2``, ... in document order, so they are stable as long
    as the document's heading structure is stable.
    """
    sections: list[tuple[str, list[str]]] = [("Overview", [])]
    for line in body.splitlines():
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(1), []))
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        chunks.append(Chunk(
            chunk_id=f"{source_id}#s{len(chunks) + 1}", source_id=source_id, source_kind=source_kind,
            service=service, title=title, section=heading, body=text, resource_uri=resource_uri,
        ))
    return chunks
