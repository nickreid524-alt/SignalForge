"""Durable event store plus in-process live fan-out.

The store is the source of truth for the event stream. A browser that disconnects and reconnects with
``Last-Event-ID: 42`` is served everything after sequence 42 from SQLite, so no event is lost to an
ephemeral queue. Live subscribers additionally receive events as they are appended; the stream
endpoint de-duplicates the overlap by sequence number.

Text passes through the shared redaction helper on the way in, so no credential can reach a browser
even if one somehow reached an event payload.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from signalforge.events.models import EventType, InvestigationEvent, Payload
from signalforge.redaction import redact

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    investigation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (investigation_id, seq)
);
CREATE INDEX IF NOT EXISTS events_by_investigation ON events (investigation_id, seq);
"""

#: A slow reader must not stall an investigation. A subscriber that exceeds this is dropped; the
#: browser reconnects with Last-Event-ID and is served the gap from SQLite.
SUBSCRIBER_QUEUE_LIMIT = 2048


class EventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + an explicit lock: the API may run its loop in a worker thread
        # (test client, embedded server) while emits come from investigation tasks.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute("SELECT MAX(version) AS v FROM event_schema_version").fetchone()
            if row["v"] is None:
                self._conn.execute("INSERT INTO event_schema_version VALUES (?, ?)",
                                   (SCHEMA_VERSION, datetime.now(UTC).isoformat()))
            elif row["v"] != SCHEMA_VERSION:
                raise RuntimeError(f"event store schema version {row['v']} != supported {SCHEMA_VERSION}")
        self._subscribers: dict[str, set[asyncio.Queue[InvestigationEvent]]] = {}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ writes
    def append(self, event: InvestigationEvent) -> InvestigationEvent:
        """Persist one event, then hand it to any live subscriber. Never raises on a slow subscriber."""
        payload = json.loads(redact(json.dumps(event.payload, default=str, ensure_ascii=False)) or "{}")
        stored = event.model_copy(update={"payload": payload})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?)",
                (stored.investigation_id, stored.seq, stored.at.isoformat(), stored.type.value,
                 json.dumps(payload, default=str, ensure_ascii=False)),
            )
        for queue in list(self._subscribers.get(stored.investigation_id, ())):
            try:
                queue.put_nowait(stored)
            except asyncio.QueueFull:  # pragma: no cover - defensive; the reader reconnects and replays
                self._subscribers[stored.investigation_id].discard(queue)
        return stored

    # ------------------------------------------------------------------ reads
    def since(self, investigation_id: str, after_seq: int = 0, limit: int = 5000) -> list[InvestigationEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE investigation_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (investigation_id, after_seq, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def last_seq(self, investigation_id: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(seq) AS s FROM events WHERE investigation_id = ?",
                                     (investigation_id,)).fetchone()
        return int(row["s"] or 0)

    def has_terminal(self, investigation_id: str) -> bool:
        from signalforge.events.models import TERMINAL_EVENTS

        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM events WHERE investigation_id = ? AND type IN (?, ?) LIMIT 1",
                (investigation_id, *[t.value for t in sorted(TERMINAL_EVENTS)]),
            ).fetchone()
        return row is not None

    def investigation_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT investigation_id FROM events ORDER BY investigation_id").fetchall()
        return [r["investigation_id"] for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> InvestigationEvent:
        return InvestigationEvent(
            seq=row["seq"], investigation_id=row["investigation_id"], type=EventType(row["type"]),
            at=datetime.fromisoformat(row["at"]), payload=json.loads(row["payload_json"]),
        )

    # ------------------------------------------------------------------ live subscription
    @contextmanager
    def subscribe(self, investigation_id: str) -> Iterator[asyncio.Queue[InvestigationEvent]]:
        """Receive events appended while subscribed. Subscribe *before* reading the backlog to avoid a gap."""
        queue: asyncio.Queue[InvestigationEvent] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
        self._subscribers.setdefault(investigation_id, set()).add(queue)
        try:
            yield queue
        finally:
            listeners = self._subscribers.get(investigation_id)
            if listeners is not None:
                listeners.discard(queue)
                if not listeners:
                    self._subscribers.pop(investigation_id, None)

    @property
    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subscribers.values())


class EventEmitter:
    """Assigns sequence numbers for one investigation and appends to a store."""

    def __init__(self, investigation_id: str, store: EventStore, *, start_seq: int | None = None) -> None:
        self.investigation_id = investigation_id
        self.store = store
        self._seq = store.last_seq(investigation_id) if start_seq is None else start_seq

    def emit(self, event_type: EventType, payload: Payload) -> InvestigationEvent:
        self._seq += 1
        event = InvestigationEvent.build(seq=self._seq, investigation_id=self.investigation_id,
                                         event_type=event_type, payload=payload)
        return self.store.append(event)


class NullEmitter:
    """Used when nobody is listening (CLI, evaluation harness, tests). Emitting is a no-op."""

    investigation_id = ""

    def emit(self, event_type: EventType, payload: Payload) -> None:
        return None


def preview(text: str | None, limit: int = 240) -> str:
    """Short, safe excerpt of assistant text for an event payload."""
    if not text:
        return ""
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


Emitter = EventEmitter | NullEmitter


def _sink(emitter: Any) -> Any:
    return emitter if emitter is not None else NullEmitter()
