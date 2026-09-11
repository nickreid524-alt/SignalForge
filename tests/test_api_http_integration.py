"""One real HTTP integration test: uvicorn on a loopback port, driven by a real HTTP client.

The rest of the API suite uses an ASGI test client, which is faster and covers behaviour. This test
exists to prove the thing actually serves over a socket, including a streaming SSE response, which
an in-process client can hide.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.http


def test_local_api_serves_a_full_investigation_over_http():
    httpx2 = pytest.importorskip("httpx2")
    pytest.importorskip("uvicorn")
    from signalforge.api.demo import local_server

    with local_server() as base:
        assert base.startswith("http://127.0.0.1:")
        with httpx2.Client(base_url=base, timeout=120.0) as http:
            assert http.get("/api/health").json()["status"] == "ok"
            meta = http.get("/api/meta").json()
            assert meta["incident_count"] == 15 and meta["uses_live_api"] is False

            created = http.post("/api/investigations", json={"incident_id": "INC-2026-0101"})
            assert created.status_code == 202
            investigation_id = created.json()["id"]

            events, sequences = [], []
            with http.stream("GET", f"/api/investigations/{investigation_id}/events") as stream:
                assert stream.status_code == 200
                assert stream.headers["content-type"].startswith("text/event-stream")
                pending = ""
                for line in stream.iter_lines():
                    if line.startswith("event:"):
                        pending = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        # the frame is only complete once its data line arrives
                        events.append(pending)
                        sequences.append(json.loads(line.split(":", 1)[1].strip())["seq"])
                        if pending in ("investigation.completed", "investigation.failed"):
                            break

            assert events[0] == "investigation.created" and events[-1] == "investigation.completed"
            assert sequences == list(range(1, len(sequences) + 1))

            detail = http.get(f"/api/investigations/{investigation_id}").json()
            assert detail["status"] == "completed" and detail["report_available"] is True
            assert http.get(f"/api/investigations/{investigation_id}/report").status_code == 200
            assert http.get(f"/api/investigations/{investigation_id}/evidence").json()["evidence"]
            assert http.get(f"/api/investigations/{investigation_id}/trace").json()["model_calls"]

            # reconnection works over a real socket too
            resumed = http.get(f"/api/investigations/{investigation_id}/events",
                               headers={"Last-Event-ID": str(sequences[-2])}).text
            assert resumed.count("event:") == 1 and "investigation.completed" in resumed


def test_the_demo_runs_end_to_end_and_refuses_what_it_should():
    pytest.importorskip("httpx2")
    pytest.importorskip("uvicorn")
    from signalforge.api.demo import run_demo

    transcript = run_demo(incident_id="INC-2026-0114", deterministic=True)
    text = transcript.text
    assert "LOCAL API DEMONSTRATION" in text and "no LLM API" in text
    assert "investigation.completed" in text and "report.completed" in text
    assert "Last-Event-ID" in text
    assert "an API key in the body   -> 400 invalid_request" in text
    assert "a live provider          -> 409 provider_unavailable" in text
    assert "<investigation-id>" in text and "http://127.0.0.1:<port>" in text
    for forbidden in ("sk-", "Authorization", "encrypted_content", "thinking"):
        assert forbidden not in text, forbidden
