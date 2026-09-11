"""The benchmark endpoints: frozen data, never a run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalforge.api.routes.evaluations import BENCHMARKS, load_benchmark
from tests.api_helpers import api_harness

GOLDEN = Path(__file__).parent / "golden" / "scripted_eval.json"


@pytest.fixture
def harness(generated):
    with api_harness(generated.snapshot) as h:
        yield h


def test_index_lists_the_available_benchmarks(harness):
    index = harness.client.get("/api/evaluations").json()
    assert index["available"] == ["scripted"]
    assert "does not run" in index["note"] or "never" in index["note"]


def test_scripted_benchmark_is_served_whole(harness):
    payload = harness.client.get("/api/evaluations/scripted").json()
    assert payload["benchmark"] == "scripted" and payload["uses_live_api"] is False
    assert payload["scenario_count"] == 15 and len(payload["scenarios"]) == 15
    aggregates = payload["aggregates"]
    for metric in ("pass_rate", "decisive_evidence_recall_mean", "decisive_citation_recall_mean",
                   "citation_validity_mean", "unsupported_claim_rate_mean", "tool_calls_total",
                   "repair_rounds_total", "calibration_ok_rate", "injection_resisted_rate"):
        assert metric in aggregates, metric
    assert aggregates["pass_rate"] == 1.0
    for scenario in payload["scenarios"]:
        assert scenario["scenario_id"].startswith("SCN-")
        assert {"expected_category", "predicted_category", "citation_validity", "tool_calls",
                "repair_rounds", "passed"} <= set(scenario)


def test_the_served_benchmark_matches_the_golden_run():
    """If the golden run changes, this artifact must be regenerated rather than silently drift."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    served = load_benchmark("scripted")
    assert served["scenarios"] == golden["scenarios"]
    assert served["aggregates"] == golden["aggregates"]
    assert served["provider"] == golden["provider"] and served["provider_mode"] == golden["provider_mode"]


def test_unknown_benchmarks_are_404(harness):
    for name in ("live", "anthropic", "../../etc/passwd", "openai"):
        response = harness.client.get(f"/api/evaluations/{name}")
        assert response.status_code == 404, name
        assert response.json()["error"]["code"] == "not_found"


def test_requesting_a_benchmark_starts_nothing(harness):
    """Loading a page must never begin an investigation, let alone a paid one."""
    before = len(harness.client.get("/api/investigations").json()["investigations"])
    for _ in range(3):
        assert harness.client.get("/api/evaluations/scripted").status_code == 200
        assert harness.client.get("/api/evaluations").status_code == 200
    after = harness.client.get("/api/investigations").json()["investigations"]
    assert len(after) == before == 0
    assert harness.runner.active == 0 and harness.runner.peak_concurrency == 0


def test_there_is_no_endpoint_that_triggers_an_evaluation(harness):
    for path in ("/api/evaluations", "/api/evaluations/scripted", "/api/evaluations/run", "/api/evaluations/live"):
        response = harness.client.post(path, json={})
        assert response.status_code in (404, 405), (path, response.status_code)
    assert harness.runner.peak_concurrency == 0


def test_the_benchmark_file_ships_with_the_package():
    path = BENCHMARKS["scripted"]
    assert path.exists() and path.parent.name == "data"
    assert path.stat().st_size > 1000
