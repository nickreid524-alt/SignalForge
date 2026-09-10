# Retrieval design (Phase 1: lexical baseline)

Corpus: 22 runbooks and 12 historical incident reviews (markdown with front matter), chunked by `##` heading.
Chunk IDs are `<source_id>#s<n>` in document order (`RB-014#s4`), so a citation can point at one section.

## Index

SQLite **FTS5** (`porter unicode61` tokenizer), in memory, built at server start (~130 chunks in milliseconds).
Columns: `chunk_id, source_id, source_kind, service, resource_uri` (unindexed) and `title, section, body`
(indexed). Ranking is `bm25()` with column weights title 4 · section 2 · body 1, then a deterministic re-rank:
×1.15 for chunks whose `service` equals the requested service, ×1.05 for `platform` runbooks. Ties break on
`chunk_id`. Results are bounded (server limit 10; retriever hard cap 50).

Verified with the stock CPython SQLite (3.49.1) on Windows; no extra dependency.

## Query sanitisation

Free text never reaches SQLite as syntax. `sanitize_query()` lowercases, extracts word tokens
(`[a-z0-9][a-z0-9_.-]{1,39}`; colons and slashes split so `col:term` filters cannot be expressed), drops
FTS operators (`AND OR NOT NEAR`) and stop-words, de-duplicates, caps at 16 terms, and emits each term as a
quoted literal joined by `OR`. An empty result means no search (not an error). Hostile inputs (`"`, `*`, `(`,
NUL, 5 000-character strings, SQL fragments) are exercised in tests.

## Interface

`Retriever` is a `Protocol`: `search(query, *, source_kind, service=None, limit=5) -> list[SearchHit]`.
`LexicalRetriever` implements it today. A future `HybridRetriever` (lexical + embeddings with reciprocal-rank
fusion) implements the same signature, so the MCP tools and later orchestration code do not change; the result
records a `strategy` string so evaluations can compare retrievers.

## Trust

Retrieved text is untrusted. Search tools return **snippets and resource URIs**, never whole documents; the full
text is fetched deliberately through `runbook://` / `incident://` resources. Every hit and every document is
typed data with a `data_notice`; the server's `instructions` and tool descriptions are constants that cannot
include corpus text. The corpus contains deliberate injection fixtures (`RB-014#s4`, `RB-020#s3`,
`INC-2026-0044#s4`) and one log-borne payload (`catalog.suspicious_query_payload`); `tests/test_security.py`
asserts they only ever surface as payload fields.
