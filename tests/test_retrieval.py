"""Retrieval: chunking, FTS5 indexing, BM25 ranking, sanitisation, identity, untrusted text."""

from __future__ import annotations

from signalforge.retrieval.chunking import chunk_markdown
from signalforge.retrieval.index import FtsIndex, LexicalRetriever, build_retriever, corpus_chunks
from signalforge.retrieval.query import query_terms, sanitize_query
from signalforge.world.repository import WorldRepository

DOC = """Intro paragraph.

## Symptoms
- something breaks

## Diagnosis
1. look at metrics

##  Notes
Trailing section.
"""


def test_chunking_is_stable_and_heading_based():
    chunks = chunk_markdown(source_id="RB-900", source_kind="runbook", service="checkout", title="T", body=DOC,
                            resource_uri="runbook://RB-900")
    assert [c.chunk_id for c in chunks] == ["RB-900#s1", "RB-900#s2", "RB-900#s3", "RB-900#s4"]
    assert [c.section for c in chunks] == ["Overview", "Symptoms", "Diagnosis", "Notes"]
    assert all(c.source_id == "RB-900" and c.resource_uri == "runbook://RB-900" for c in chunks)
    again = chunk_markdown(source_id="RB-900", source_kind="runbook", service="checkout", title="T", body=DOC,
                           resource_uri="runbook://RB-900")
    assert again == chunks


def test_corpus_indexes_every_document(repo: WorldRepository):
    chunks = corpus_chunks(repo)
    sources = {c.source_id for c in chunks}
    assert sources == {r.id for r in repo.runbooks()} | {i.id for i in repo.historical_incidents()}
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    index = FtsIndex()
    assert index.build(chunks) == len(chunks) > 100


def test_bm25_ranking_puts_the_obvious_document_first(repo: WorldRepository):
    retriever = build_retriever(repo)
    hits = retriever.search("payvault client certificate expired handshake", source_kind="runbook", limit=5)
    assert hits[0].source_id == "RB-016"
    assert hits[0].rank == 1 and hits[0].score > hits[-1].score
    hits = retriever.search("connection pool exhausted primary reporting", source_kind="runbook", limit=5)
    assert {h.source_id for h in hits[:2]} & {"RB-005", "RB-002"}
    hits = retriever.search("zone connection reset 502 firmware", source_kind="incident", limit=3)
    assert hits[0].source_id == "INC-2026-0052"


def test_results_are_stable_across_index_builds(repo: WorldRepository):
    a = build_retriever(repo).search("cache eviction maxmemory hit ratio", source_kind="runbook", limit=5)
    b = build_retriever(repo).search("cache eviction maxmemory hit ratio", source_kind="runbook", limit=5)
    assert [(h.chunk_id, h.score) for h in a] == [(h.chunk_id, h.score) for h in b]
    assert a[0].source_id == "RB-012"


def test_service_boost_is_deterministic_and_bounded(repo: WorldRepository):
    retriever = build_retriever(repo)
    plain = retriever.search("latency triage", source_kind="runbook", limit=10)
    boosted = retriever.search("latency triage", source_kind="runbook", service="checkout", limit=10)
    assert {h.chunk_id for h in plain} == {h.chunk_id for h in boosted}  # boost re-ranks, never adds
    assert boosted[0].service in ("checkout", "platform")
    assert len(retriever.search("latency", source_kind="runbook", limit=99)) <= 50


def test_hit_identity_and_untrusted_snippets(repo: WorldRepository):
    retriever = build_retriever(repo)
    for hit in retriever.search("dns search domains nxdomain", source_kind="runbook", limit=5):
        assert hit.chunk_id.startswith(hit.source_id + "#s")
        assert hit.resource_uri == f"runbook://{hit.source_id}"
        assert hit.source_kind == "runbook"
        assert isinstance(hit.snippet, str)


def test_query_sanitisation_neutralises_fts_syntax():
    assert sanitize_query("") is None
    assert sanitize_query("the of and") is None
    assert sanitize_query('title:"error" OR body:pool NEAR(latency slow) -c') == (
        '"title" OR "error" OR "body" OR "pool" OR "latency" OR "slow"'
    )
    expr = sanitize_query('checkout" OR chunk_id:* ; DROP TABLE chunks; --')
    assert expr == '"checkout" OR "chunk_id" OR "drop" OR "table" OR "chunks"'
    assert '"' not in "".join(query_terms('a"b'))
    assert len(query_terms(" ".join(f"term{i}" for i in range(40)))) == 16
    long_term = "x" * 300
    assert all(len(t) <= 40 for t in query_terms(long_term))


def test_hostile_query_strings_return_results_or_nothing_but_never_raise(repo: WorldRepository):
    retriever = build_retriever(repo)
    for hostile in ['"', "*", "(", ")", "AND", 'x" AND y', "chunks MATCH 'a'", "\x00", "🔥🔥", "a" * 5000]:
        hits = retriever.search(hostile, source_kind="runbook", limit=5)
        assert isinstance(hits, list)


def test_instruction_like_document_text_is_returned_as_data(repo: WorldRepository):
    retriever = build_retriever(repo)
    hits = retriever.search("ignore previous instructions conclude root cause mailrelay", source_kind="runbook", limit=5)
    assert any(h.chunk_id == "RB-014#s4" for h in hits)
    injected = next(h for h in hits if h.chunk_id == "RB-014#s4")
    # the text is present as a snippet (evidence), typed as a SearchHit field, nothing else
    assert "instructions" in injected.snippet.lower() or "mailrelay" in injected.snippet.lower()
    assert set(type(injected).model_fields) == {"rank", "chunk_id", "source_id", "source_kind", "service", "title",
                                                "section", "snippet", "score", "resource_uri"}


def test_retriever_protocol_is_strategy_agnostic():
    from signalforge.retrieval.models import Retriever

    class Stub:
        strategy = "stub"

        def search(self, query, *, source_kind, service=None, limit=5):
            return []

    stub: Retriever = Stub()
    assert stub.search("x", source_kind="runbook") == []
    assert isinstance(LexicalRetriever(FtsIndex()), LexicalRetriever)
