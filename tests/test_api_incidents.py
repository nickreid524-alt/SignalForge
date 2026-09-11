"""The incident queue over HTTP, and the ground-truth firewall at the API boundary.

These tests may import the scenario catalogue: they are on the evaluation side of the firewall and
exist precisely to check that the serialized API output does not contain what they can see.
"""

from __future__ import annotations

import json
import re

import pytest

from signalforge.scenarios.catalogue import SCENARIOS
from tests.api_helpers import api_harness


@pytest.fixture
def harness(generated):
    with api_harness(generated.snapshot) as h:
        yield h


def test_incident_listing(harness):
    incidents = harness.client.get("/api/incidents").json()["incidents"]
    assert len(incidents) == 15
    assert [i["id"] for i in incidents] == sorted(i["id"] for i in incidents)
    for incident in incidents:
        assert set(incident) == {"id", "title", "severity", "affected_service", "detected_at",
                                 "investigation_clock", "reporter"}


def test_incident_detail_adds_only_the_description(harness):
    detail = harness.client.get("/api/incidents/INC-2026-0101").json()
    assert detail["id"] == "INC-2026-0101" and detail["affected_service"] == "checkout"
    assert set(detail) == {"id", "title", "severity", "affected_service", "detected_at",
                           "investigation_clock", "reporter", "description"}
    assert harness.client.get("/api/incidents/INC-2026-9999").status_code == 404


def test_invalid_incident_identifiers_are_rejected(harness):
    for bad in ("nope", "../../etc/passwd", "INC-1", "INC-2026-01011", "%2e%2e", "INC-2026-010a"):
        response = harness.client.get(f"/api/incidents/{bad}")
        assert response.status_code in (400, 404), (bad, response.status_code)
        assert response.json()["error"]["code"] in ("invalid_request", "not_found")


def test_serialized_incidents_cannot_reveal_ground_truth(harness):
    """The strongest statement this API makes: the browser sees what an investigator sees, and no more."""
    listing = harness.client.get("/api/incidents").text
    details = "".join(harness.client.get(f"/api/incidents/{s.incident_id}").text for s in SCENARIOS)
    served = (listing + details).lower()

    for spec in SCENARIOS:
        assert spec.id.lower() not in served, spec.id                       # no SCN-xx scenario ids
        assert spec.root_cause_statement.lower() not in served, spec.id     # no root cause
        assert spec.category.lower() not in served, spec.category           # no failure category
        assert re.search(rf"\b{re.escape(spec.difficulty.lower())}\b", served) is None, spec.difficulty
        for handle in spec.root_cause_handles:
            assert handle.lower() not in served, handle                     # no fault handles
        for tool in spec.expected_useful_tools:
            assert tool.lower() not in served, tool                         # no expected-tool hints
        for misleading in spec.misleading_evidence:
            assert misleading.description.lower() not in served
        for unacceptable in spec.unacceptable_conclusions:
            assert unacceptable.description.lower() not in served

    # Service names are not secrets (the topology resource exposes the whole graph); the secret is which
    # service is to blame for a given incident. Check that per incident, not across the whole corpus.
    for spec in SCENARIOS:
        document = harness.client.get(f"/api/incidents/{spec.incident_id}").text.lower()
        assert spec.root_cause_statement.lower() not in document, spec.id
        assert spec.category.lower() not in document, spec.id

    for word in ("root_cause", "root cause", "decisive", "red herring", "red_herring", "misleading",
                 "ground truth", "ground_truth", "culprit", "answer", "expected_", "rubric"):
        assert word not in served, word


def test_meta_incident_count_comes_from_the_world_not_the_answer_key(harness):
    """The count is 15 because the world holds 15 open incidents, not because a scenario file says so."""
    meta = harness.client.get("/api/meta").json()
    assert meta["incident_count"] == len(harness.client.get("/api/incidents").json()["incidents"])
    assert "scenario" not in json.dumps(meta).lower()


def test_api_package_never_imports_ground_truth():
    """A static guarantee, independent of any response body."""
    import ast
    from pathlib import Path

    api = Path(__file__).resolve().parents[1] / "src" / "signalforge" / "api"
    files = sorted(api.rglob("*.py"))
    assert files
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = {m for m in imported if m.startswith(("signalforge.scenarios", "signalforge.evals"))}
        assert not offenders, f"{file.name} imports {offenders}"
