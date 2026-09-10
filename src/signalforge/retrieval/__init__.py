"""Deterministic local retrieval over runbooks and historical incidents (SQLite FTS5, BM25)."""

from signalforge.retrieval.chunking import chunk_markdown
from signalforge.retrieval.index import FtsIndex, LexicalRetriever, build_retriever
from signalforge.retrieval.models import Chunk, Retriever, SearchHit, SourceKind
from signalforge.retrieval.query import sanitize_query

__all__ = [
    "Chunk",
    "FtsIndex",
    "LexicalRetriever",
    "Retriever",
    "SearchHit",
    "SourceKind",
    "build_retriever",
    "chunk_markdown",
    "sanitize_query",
]
