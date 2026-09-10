"""Retrieval data model and the strategy-agnostic Retriever interface."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

SourceKind = Literal["runbook", "incident"]


class Chunk(BaseModel):
    """A heading-level section of a corpus document. ``chunk_id`` is ``<source_id>#s<n>``."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_id: str
    source_kind: SourceKind
    service: str
    title: str
    section: str
    body: str
    resource_uri: str


class SearchHit(BaseModel):
    """One retrieval result. ``snippet`` is untrusted document text."""

    model_config = ConfigDict(extra="forbid")

    rank: int
    chunk_id: str
    source_id: str
    source_kind: SourceKind
    service: str
    title: str
    section: str
    snippet: str
    score: float
    resource_uri: str


class Retriever(Protocol):
    """What orchestration code depends on. Lexical today; hybrid/semantic later, same signature."""

    def search(self, query: str, *, source_kind: SourceKind, service: str | None = None,
               limit: int = 5) -> list[SearchHit]: ...
