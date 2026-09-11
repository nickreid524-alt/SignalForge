"""The Server-Sent Events contract: ordering, sequence ids, reconnection, replay and termination."""

from __future__ import annotations

import json

import pytest

from signalforge.events.models import EVENT_PAYLOADS, TERMINAL_EVENTS, EventType, InvestigationEvent
from tests.api_helpers import (
    FailingProvider,
    SlowScriptedProvider,
    api_harness,
    parse_sse,
    start,
    stream_until,
    wait_for_terminal,
)

HIDDEN_REASONING_MARKERS = ("opaque", "thinking", "redacted_thinking", "encrypted_content", "reasoning_content",
                            "signature", "summary_text")


@pytest.fixture(scope="module")
def streamed(generated):
    """One investigation watched from the first event to the last."""
    with api_harness(generated.snapshot) as h:
        investigation_id = start(h, "INC-2026-0106")
        frames = stream_until(h, investigation_id)
        yield h, investigation_id, frames


# ---------------------------------------------------------------------- the stream itself


def test_stream_is_ordered_gap_free_and_terminates(streamed):
    _, investigation_id, frames = streamed
    assert len(frames) > 20
    sequences = [f["id"] for f in frames]
    assert sequences == list(range(1, len(frames) + 1)), "sequence ids must be monotonic and gap-free"
    assert len({f["id"] for f in frames}) == len(frames), "sequence ids must be unique"
    assert frames[0]["event"] == "investigation.created"
    assert frames[-1]["event"] == "investigation.completed"
    assert all(f["event"] not in TERMINAL_EVENTS for f in frames[:-1]), "exactly one terminal event"
    for frame in frames:
        assert frame["data"]["seq"] == frame["id"]           # the SSE id and the payload agree
        assert frame["data"]["investigation_id"] == investigation_id
        assert frame["data"]["type"] == frame["event"]
        assert frame["data"]["at"]


def test_the_stream_tells_the_story_of_the_investigation(streamed):
    _, _, frames = streamed
    types = [f["event"] for f in frames]
    for expected in ("investigation.created", "status.changed", "step.started", "provider.completed",
                     "tool.requested", "tool.completed", "evidence.registered", "hypothesis.updated",
                     "validation.started", "report.completed", "investigation.completed"):
        assert expected in types, expected
    # causality: a tool result never precedes its request, and evidence exists before the report
    first_request = types.index("tool.requested")
    assert first_request < types.index("tool.completed")
    assert types.index("evidence.registered") < types.index("report.completed")
    assert types.index("step.started") < types.index("provider.completed")


def test_every_payload_matches_its_declared_model(streamed):
    """The event schema is a contract, not a loose dictionary."""
    _, _, frames = streamed
    seen: set[str] = set()
    for frame in frames:
        event_type = EventType(frame["event"])
        model = EVENT_PAYLOADS[event_type]
        model.model_validate(frame["data"]["payload"])       # extra="forbid": no stray field may appear
        seen.add(event_type.value)
    assert len(seen) >= 10


def test_no_hidden_reasoning_reaches_the_stream(streamed):
    _, _, frames = streamed
    body = json.dumps(frames).lower()
    for marker in HIDDEN_REASONING_MARKERS:
        assert marker not in body, marker
    assert "sk-" not in body


def test_provider_completed_reports_usage_honestly(streamed):
    _, _, frames = streamed
    calls = [f["data"]["payload"] for f in frames if f["event"] == "provider.completed"]
    assert calls
    for call in calls:
        assert call["usage_reported"] is False               # the scripted provider is not an LLM
        assert call["input_tokens"] is None and call["output_tokens"] is None
        assert call["purpose"] in ("deliberate", "report", "repair")


# ---------------------------------------------------------------------- reconnection


def test_reconnect_with_last_event_id_replays_exactly_what_was_missed(streamed):
    harness, investigation_id, frames = streamed
    total = len(frames)
    for cursor in (0, 1, 5, total // 2, total - 1, total):
        response = harness.client.get(f"/api/investigations/{investigation_id}/events",
                                      headers={"Last-Event-ID": str(cursor)})
        assert response.status_code == 200
        replayed = parse_sse(response.text)
        assert [f["id"] for f in replayed] == list(range(cursor + 1, total + 1)), cursor
        assert [f["event"] for f in replayed] == [f["event"] for f in frames[cursor:]]


def test_the_after_query_parameter_is_equivalent_to_the_header(streamed):
    harness, investigation_id, frames = streamed
    by_header = parse_sse(harness.client.get(f"/api/investigations/{investigation_id}/events",
                                             headers={"Last-Event-ID": "4"}).text)
    by_query = parse_sse(harness.client.get(f"/api/investigations/{investigation_id}/events?after=4").text)
    assert [f["id"] for f in by_header] == [f["id"] for f in by_query] == list(range(5, len(frames) + 1))


def test_connecting_to_a_finished_investigation_replays_and_closes(streamed):
    harness, investigation_id, frames = streamed
    replayed = stream_until(harness, investigation_id)     # a fresh connection, nothing still running
    assert [f["id"] for f in replayed] == [f["id"] for f in frames]
    assert replayed[-1]["event"] == "investigation.completed"


def test_disconnect_midway_then_reconnect_loses_nothing(generated):
    """The real browser case: drop the connection while the investigation is still running."""
    with api_harness(generated.snapshot, provider_factory=lambda *a, **k: SlowScriptedProvider(0.08)) as h:
        investigation_id = start(h, "INC-2026-0101")
        first = stream_until(h, investigation_id, stop_after=6)          # disconnects on leaving the block
        assert len(first) == 6 and first[-1]["event"] not in TERMINAL_EVENTS
        last_seen = first[-1]["id"]

        wait_for_terminal(h, investigation_id)                            # time passes while nobody listens
        rest = stream_until(h, investigation_id, headers={"Last-Event-ID": str(last_seen)})

        assert [f["id"] for f in rest] == list(range(last_seen + 1, last_seen + 1 + len(rest)))
        assert rest[-1]["event"] == "investigation.completed"
        combined = first + rest
        assert [f["id"] for f in combined] == list(range(1, len(combined) + 1))   # no gap, no duplicate


def test_an_invalid_cursor_is_rejected(streamed):
    harness, investigation_id, _ = streamed
    for bad in ("abc", "-1", "99999999999"):
        response = harness.client.get(f"/api/investigations/{investigation_id}/events",
                                      headers={"Last-Event-ID": bad})
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "invalid_request"


def test_streaming_an_unknown_investigation_is_404(streamed):
    harness, _, _ = streamed
    assert harness.client.get("/api/investigations/inv-nope/events").status_code == 404
    assert harness.client.get("/api/investigations/not a valid id/events").status_code in (400, 404)


# ---------------------------------------------------------------------- failure


def test_a_failing_investigation_ends_with_a_failure_event(generated):
    with api_harness(generated.snapshot, provider_factory=lambda *a, **k: FailingProvider()) as h:
        investigation_id = start(h, "INC-2026-0101")
        frames = stream_until(h, investigation_id)
        assert frames[-1]["event"] == "investigation.failed"
        payload = frames[-1]["data"]["payload"]
        assert payload["terminal_status"] == "failed"
        assert payload["category"] == "authentication"
        assert "invalid credential" in payload["message"]
        assert "sk-" not in json.dumps(frames)
        # the stream still closed cleanly, and the ids are still contiguous
        assert [f["id"] for f in frames] == list(range(1, len(frames) + 1))


# ---------------------------------------------------------------------- the durable store


def test_the_event_store_is_the_durable_source(streamed):
    """Replay does not depend on a live queue: the events are in SQLite."""
    harness, investigation_id, frames = streamed
    stored = harness.services.events.since(investigation_id, 0)
    assert [e.seq for e in stored] == [f["id"] for f in frames]
    assert [e.type.value for e in stored] == [f["event"] for f in frames]
    assert harness.services.events.last_seq(investigation_id) == len(frames)
    assert harness.services.events.has_terminal(investigation_id) is True
    assert harness.services.events.subscriber_count == 0, "every subscription was released"


def test_events_are_separate_from_the_trace_schema(streamed):
    """The UI contract does not expose SQLite column names, so the trace can evolve freely."""
    harness, investigation_id, frames = streamed
    trace_columns = {"investigation_id", "request_fingerprint", "payload_json", "arguments_json", "issues_json",
                     "response_json", "budget_json", "usage_json", "source_ids_json", "seq_id"}
    for frame in frames:
        assert not (set(frame["data"]["payload"]) & trace_columns), frame["event"]
    trace = harness.client.get(f"/api/investigations/{investigation_id}/trace").json()
    assert "_json" not in json.dumps(trace)


def test_event_construction_rejects_a_mismatched_payload():
    with pytest.raises(TypeError, match="expects"):
        InvestigationEvent.build(seq=1, investigation_id="inv-x", event_type=EventType.STATUS_CHANGED,
                                 payload=EVENT_PAYLOADS[EventType.STEP_STARTED](step=1))


def test_every_event_type_has_a_payload_model():
    assert set(EVENT_PAYLOADS) == set(EventType)
    for event_type, model in EVENT_PAYLOADS.items():
        assert model.model_config.get("extra") == "forbid", event_type
