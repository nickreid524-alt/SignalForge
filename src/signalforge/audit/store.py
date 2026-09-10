"""Investigation trace persistence (SQLite, explicit schema version).

Every provider call, requested action, MCP call, evidence item, hypothesis
update, validation round, repair request and the final report are recorded.
Secrets never enter the store: all free text passes through ``redact``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\b(\s*[:=]\s*)([^\s,;'\"]{6,})"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    out = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        else:
            out = pattern.sub("[REDACTED]", out)
    return out


def _json(value: Any) -> str:
    return redact(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)) or "null"


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, provider_name TEXT, provider_model TEXT, provider_mode TEXT,
    uses_llm INTEGER, transport TEXT, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,
    termination_reason TEXT, budget_json TEXT NOT NULL, usage_json TEXT, incident_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS status_changes (
    investigation_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL, from_status TEXT NOT NULL,
    to_status TEXT NOT NULL, note TEXT, PRIMARY KEY (investigation_id, seq)
);
CREATE TABLE IF NOT EXISTS steps (
    investigation_id TEXT NOT NULL, step INTEGER NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
    model_call_id TEXT, assistant_text TEXT, finish_requested INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (investigation_id, step)
);
CREATE TABLE IF NOT EXISTS model_calls (
    id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL, step INTEGER, purpose TEXT NOT NULL, provider_name TEXT,
    request_fingerprint TEXT, tool_count INTEGER, latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER,
    stop_reason TEXT, response_json TEXT, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    investigation_id TEXT NOT NULL, step INTEGER NOT NULL, seq INTEGER NOT NULL, request_id TEXT, kind TEXT NOT NULL,
    name TEXT NOT NULL, arguments_json TEXT, accepted INTEGER NOT NULL, rejection_code TEXT, rejection_reason TEXT,
    evidence_id TEXT, duplicate_of TEXT, ok INTEGER, error TEXT, latency_ms REAL,
    PRIMARY KEY (investigation_id, step, seq)
);
CREATE TABLE IF NOT EXISTS evidence (
    investigation_id TEXT NOT NULL, evidence_id TEXT NOT NULL, sequence INTEGER NOT NULL, acquired_at TEXT NOT NULL,
    source_kind TEXT NOT NULL, source_name TEXT NOT NULL, arguments_json TEXT, ok INTEGER NOT NULL, error TEXT,
    result_kind TEXT, source_ids_json TEXT NOT NULL, content_hash TEXT NOT NULL, payload_json TEXT, text TEXT,
    latency_ms REAL, PRIMARY KEY (investigation_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS hypothesis_updates (
    investigation_id TEXT NOT NULL, seq INTEGER NOT NULL, step INTEGER NOT NULL, hypothesis_id TEXT NOT NULL,
    statement TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL, supporting_json TEXT, contradicting_json TEXT,
    note TEXT, PRIMARY KEY (investigation_id, seq)
);
CREATE TABLE IF NOT EXISTS validations (
    investigation_id TEXT NOT NULL, round INTEGER NOT NULL, ok INTEGER NOT NULL, error_count INTEGER NOT NULL,
    warning_count INTEGER NOT NULL, issues_json TEXT NOT NULL, draft_json TEXT, parse_error TEXT, created_at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, round)
);
CREATE TABLE IF NOT EXISTS repairs (
    investigation_id TEXT NOT NULL, round INTEGER NOT NULL, request_text TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, round)
);
CREATE TABLE IF NOT EXISTS reports (
    investigation_id TEXT PRIMARY KEY, report_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class TraceStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            if row["v"] is None:
                self._conn.execute("INSERT INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION, _now()))
            elif row["v"] != SCHEMA_VERSION:
                raise RuntimeError(f"trace store schema version {row['v']} != supported {SCHEMA_VERSION}")
        self._seq: dict[tuple[str, str], int] = {}

    def close(self) -> None:
        self._conn.close()

    def _next(self, investigation_id: str, table: str) -> int:
        key = (investigation_id, table)
        self._seq[key] = self._seq.get(key, 0) + 1
        return self._seq[key]

    # ------------------------------------------------------------------ writes
    def start_investigation(self, *, investigation_id: str, incident_id: str, provider: dict[str, Any],
                            transport: str, budget: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO investigations (id, incident_id, provider_name, provider_model, provider_mode, uses_llm, transport,"
                " started_at, status, budget_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (investigation_id, incident_id, provider.get("name"), provider.get("model"), provider.get("mode"),
                 int(bool(provider.get("uses_llm"))), transport, _now(), "created", _json(budget)),
            )

    def record_incident(self, investigation_id: str, incident: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute("UPDATE investigations SET incident_json = ? WHERE id = ?", (_json(incident), investigation_id))

    def record_status_change(self, investigation_id: str, change: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO status_changes VALUES (?,?,?,?,?,?)",
                (investigation_id, self._next(investigation_id, "status_changes"), str(change["at"]), change["from_status"],
                 change["to_status"], redact(change.get("note", ""))),
            )

    def record_step(self, investigation_id: str, step: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO steps VALUES (?,?,?,?,?,?,?)",
                (investigation_id, step["step"], str(step["started_at"]), str(step.get("ended_at")) if step.get("ended_at") else None,
                 step.get("model_call_id"), redact(step.get("assistant_text")), int(bool(step.get("finish_requested")))),
            )

    def record_model_call(self, investigation_id: str, call: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (call["id"], investigation_id, call.get("step"), call["purpose"], call.get("provider_name"),
                 call.get("request_fingerprint"), call.get("tool_count"), call.get("latency_ms"), call.get("input_tokens"),
                 call.get("output_tokens"), call.get("stop_reason"), _json(call.get("response")), redact(call.get("error")), _now()),
            )

    def record_action(self, investigation_id: str, step: int, outcome: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (investigation_id, step, self._next(investigation_id, f"actions:{step}"), outcome.get("request_id"), outcome["kind"],
                 outcome["name"], _json(outcome.get("arguments", {})), int(bool(outcome["accepted"])), outcome.get("rejection_code"),
                 redact(outcome.get("rejection_reason")), outcome.get("evidence_id"), outcome.get("duplicate_of"),
                 None if outcome.get("ok") is None else int(bool(outcome.get("ok"))), redact(outcome.get("error")),
                 outcome.get("latency_ms")),
            )

    def record_evidence(self, investigation_id: str, item: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (investigation_id, item["evidence_id"], item["sequence"], str(item["acquired_at"]), item["source_kind"],
                 item["source_name"], _json(item.get("arguments", {})), int(bool(item["ok"])), redact(item.get("error")),
                 item.get("result_kind"), _json(item.get("source_ids", [])), item["content_hash"],
                 _json(item.get("payload")) if item.get("payload") is not None else None, redact(item.get("text")),
                 item.get("latency_ms")),
            )

    def record_hypothesis(self, investigation_id: str, step: int, hypothesis: dict[str, Any], note: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO hypothesis_updates VALUES (?,?,?,?,?,?,?,?,?,?)",
                (investigation_id, self._next(investigation_id, "hypothesis_updates"), step, hypothesis["id"],
                 redact(hypothesis["statement"]), hypothesis["confidence"], hypothesis["status"],
                 _json(hypothesis.get("supporting_evidence_ids", [])), _json(hypothesis.get("contradicting_evidence_ids", [])),
                 redact(note)),
            )

    def record_validation(self, investigation_id: str, round_index: int, *, ok: bool, issues: list[dict[str, Any]],
                          draft: dict[str, Any] | None, parse_error: str | None) -> None:
        errors = sum(1 for i in issues if i.get("severity") == "error")
        warnings = sum(1 for i in issues if i.get("severity") == "warning")  # info-level notes are not warnings
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO validations VALUES (?,?,?,?,?,?,?,?,?)",
                (investigation_id, round_index, int(ok), errors, warnings, _json(issues),
                 _json(draft) if draft is not None else None, redact(parse_error), _now()),
            )

    def record_repair(self, investigation_id: str, round_index: int, request_text: str) -> None:
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO repairs VALUES (?,?,?,?)",
                               (investigation_id, round_index, redact(request_text), _now()))

    def record_report(self, investigation_id: str, report: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?)", (investigation_id, _json(report), _now()))

    def finish_investigation(self, investigation_id: str, *, status: str, termination_reason: str | None,
                             usage: dict[str, Any], error: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE investigations SET ended_at = ?, status = ?, termination_reason = ?, usage_json = ?, error = ? WHERE id = ?",
                (_now(), status, termination_reason, _json(usage), redact(error), investigation_id),
            )

    # ------------------------------------------------------------------ reads
    def _rows(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def list_investigations(self) -> list[dict[str, Any]]:
        return self._rows("SELECT id, incident_id, provider_name, provider_mode, status, started_at, ended_at, termination_reason "
                          "FROM investigations ORDER BY started_at, id", ())

    def load(self, investigation_id: str) -> dict[str, Any] | None:
        head = self._rows("SELECT * FROM investigations WHERE id = ?", (investigation_id,))
        if not head:
            return None
        inv = head[0]
        for key in ("budget_json", "usage_json", "incident_json"):
            inv[key.removesuffix("_json")] = json.loads(inv.pop(key)) if inv.get(key) else None
        return {
            "investigation": inv,
            "status_changes": self._rows("SELECT * FROM status_changes WHERE investigation_id = ? ORDER BY seq", (investigation_id,)),
            "steps": self._rows("SELECT * FROM steps WHERE investigation_id = ? ORDER BY step", (investigation_id,)),
            "model_calls": [self._decode(r, "response_json") for r in
                            self._rows("SELECT * FROM model_calls WHERE investigation_id = ? ORDER BY created_at, id", (investigation_id,))],
            "actions": [self._decode(r, "arguments_json") for r in
                        self._rows("SELECT * FROM actions WHERE investigation_id = ? ORDER BY step, seq", (investigation_id,))],
            "evidence": [self._decode(self._decode(self._decode(r, "arguments_json"), "source_ids_json"), "payload_json") for r in
                         self._rows("SELECT * FROM evidence WHERE investigation_id = ? ORDER BY sequence", (investigation_id,))],
            "hypothesis_updates": [self._decode(self._decode(r, "supporting_json"), "contradicting_json") for r in
                                   self._rows("SELECT * FROM hypothesis_updates WHERE investigation_id = ? ORDER BY seq", (investigation_id,))],
            "validations": [self._decode(self._decode(r, "issues_json"), "draft_json") for r in
                            self._rows("SELECT * FROM validations WHERE investigation_id = ? ORDER BY round", (investigation_id,))],
            "repairs": self._rows("SELECT * FROM repairs WHERE investigation_id = ? ORDER BY round", (investigation_id,)),
            "report": (lambda rows: json.loads(rows[0]["report_json"]) if rows else None)(
                self._rows("SELECT report_json FROM reports WHERE investigation_id = ?", (investigation_id,))),
        }

    @staticmethod
    def _decode(row: dict[str, Any], key: str) -> dict[str, Any]:
        value = row.pop(key, None)
        row[key.removesuffix("_json")] = json.loads(value) if value else None
        return row
