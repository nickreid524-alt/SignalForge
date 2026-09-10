"""SQLite FTS5 index and the lexical BM25 retriever."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable

from signalforge.retrieval.chunking import chunk_markdown
from signalforge.retrieval.models import Chunk, SearchHit, SourceKind
from signalforge.retrieval.query import sanitize_query
from signalforge.world.repository import WorldRepository

# column order in the virtual table; bm25 weights must follow it
_COLUMNS = ("chunk_id", "source_id", "source_kind", "service", "resource_uri", "title", "section", "body")
_BM25_WEIGHTS = (0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 2.0, 1.0)
_BODY_COLUMN = _COLUMNS.index("body")
SERVICE_BOOST = 1.15
PLATFORM_BOOST = 1.05


class FtsIndex:
    """Thread-safe wrapper around an FTS5 table. Handlers may run on worker threads."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "chunk_id UNINDEXED, source_id UNINDEXED, source_kind UNINDEXED, service UNINDEXED, "
            "resource_uri UNINDEXED, title, section, body, tokenize='porter unicode61')"
        )
        self.size = 0

    def build(self, chunks: Iterable[Chunk]) -> int:
        rows = [(c.chunk_id, c.source_id, c.source_kind, c.service, c.resource_uri, c.title, c.section, c.body)
                for c in chunks]
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks")
            self._conn.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)", rows)
        self.size = len(rows)
        return self.size

    def search_expression(self, expression: str, *, source_kind: SourceKind, limit: int) -> list[tuple]:
        """Raw candidates ordered by bm25 (lower is better) then chunk_id. ``expression`` must be sanitised."""
        weights = ", ".join(str(w) for w in _BM25_WEIGHTS)
        sql = (
            f"SELECT chunk_id, source_id, source_kind, service, resource_uri, title, section, "
            f"bm25(chunks, {weights}) AS score, snippet(chunks, {_BODY_COLUMN}, '[', ']', '…', 18) "
            f"FROM chunks WHERE chunks MATCH ? AND source_kind = ? ORDER BY score ASC, chunk_id ASC LIMIT ?"
        )
        with self._lock:
            return self._conn.execute(sql, (expression, source_kind, limit)).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class LexicalRetriever:
    """BM25 over FTS5 with a deterministic service boost and chunk-id tie-breaking."""

    strategy = "lexical-fts5-bm25"

    def __init__(self, index: FtsIndex) -> None:
        self.index = index

    def search(self, query: str, *, source_kind: SourceKind, service: str | None = None,
               limit: int = 5) -> list[SearchHit]:
        limit = max(1, min(limit, 50))
        expression = sanitize_query(query)
        if expression is None:
            return []
        candidates = self.index.search_expression(expression, source_kind=source_kind, limit=min(limit * 4, 80))
        scored: list[tuple[float, str, tuple]] = []
        for row in candidates:
            relevance = -float(row[7])  # bm25 is negative; larger relevance is better
            chunk_service = row[3]
            if service and chunk_service == service:
                relevance *= SERVICE_BOOST
            elif service and chunk_service == "platform":
                relevance *= PLATFORM_BOOST
            scored.append((relevance, row[0], row))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits: list[SearchHit] = []
        for rank, (relevance, _, row) in enumerate(scored[:limit], start=1):
            hits.append(SearchHit(
                rank=rank, chunk_id=row[0], source_id=row[1], source_kind=row[2], service=row[3],
                resource_uri=row[4], title=row[5], section=row[6], snippet=row[8] or "",
                score=round(relevance, 4),
            ))
        return hits


def corpus_chunks(repo: WorldRepository) -> list[Chunk]:
    chunks: list[Chunk] = []
    for rb in repo.runbooks():
        chunks.extend(chunk_markdown(source_id=rb.id, source_kind="runbook", service=rb.service, title=rb.title,
                                     body=rb.body, resource_uri=f"runbook://{rb.id}"))
    for inc in repo.historical_incidents():
        chunks.extend(chunk_markdown(source_id=inc.id, source_kind="incident", service=inc.service, title=inc.title,
                                     body=inc.body, resource_uri=f"incident://{inc.id}"))
    return chunks


def build_retriever(repo: WorldRepository, path: str = ":memory:") -> LexicalRetriever:
    index = FtsIndex(path)
    index.build(corpus_chunks(repo))
    return LexicalRetriever(index)
