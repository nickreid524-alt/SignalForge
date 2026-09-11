"""Server-Sent Events for a running investigation, with durable replay.

The ordering matters and is the whole design:

1. subscribe to live events first, so nothing appended during step 2 is missed;
2. read the backlog after ``Last-Event-ID`` from SQLite, which is the durable source;
3. switch to the live queue, discarding anything already sent by sequence number;
4. stop at the terminal event.

A client that disconnects and reconnects with ``Last-Event-ID`` therefore receives exactly the events
it missed, in order, whether or not the investigation was still running while it was away.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from signalforge.api.schemas import bounded_int
from signalforge.events.models import InvestigationEvent
from signalforge.events.store import EventStore

#: Comment frames every few seconds keep proxies and idle connections from closing the stream.
PING_SECONDS = 15
MAX_BACKLOG = 5000


def last_event_id(headers: Any, query: Any) -> int:
    """The reconnection cursor: the standard ``Last-Event-ID`` header, or an explicit ``?after=``."""
    raw = headers.get("last-event-id")
    if raw is None or str(raw).strip() == "":
        raw = query.get("after")
    return bounded_int(raw, default=0, low=0, high=10_000_000, what="last event id")


def frame(event: InvestigationEvent) -> dict[str, str]:
    """One SSE frame. ``id`` is the per-investigation sequence, which is what the client sends back."""
    return {
        "id": str(event.seq),
        "event": event.type.value,
        "data": json.dumps({
            "seq": event.seq,
            "investigation_id": event.investigation_id,
            "type": event.type.value,
            "at": event.at.isoformat(),
            "payload": event.payload,
        }, default=str),
    }


async def event_stream(store: EventStore, investigation_id: str, after_seq: int = 0) -> AsyncIterator[dict[str, str]]:
    with store.subscribe(investigation_id) as queue:
        sent = after_seq
        for event in store.since(investigation_id, after_seq, limit=MAX_BACKLOG):
            yield frame(event)
            sent = event.seq
            if event.is_terminal:
                return
        # Nothing more is coming for an investigation that already finished: close rather than hang.
        if store.has_terminal(investigation_id):
            return
        while True:
            event = await queue.get()
            if event.seq <= sent:
                continue  # already delivered from the backlog
            yield frame(event)
            sent = event.seq
            if event.is_terminal:
                return
