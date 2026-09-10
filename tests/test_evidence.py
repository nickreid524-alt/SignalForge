"""Evidence registry and citation grammar."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from signalforge.evidence.citations import CitationValidator, extract_citations, parse_citation
from signalforge.evidence.registry import EvidenceRegistry, canonical_json, content_hash

FIXED = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _payload(kind: str = "deployments", ids: list[str] | None = None) -> dict:
    return {"kind": kind, "source_ids": ids if ids is not None else ["DEP-0001", "DEP-0002"], "summary": "s"}


def test_sequential_ids_and_order():
    reg = EvidenceRegistry("inv-1", clock=lambda: FIXED)
    a = reg.register_tool_result(name="get_deployments", arguments={}, payload=_payload(), text="{}", ok=True,
                                 error=None, latency_ms=1.0)
    b = reg.register_resource(uri="runbook://RB-016", text="# x", payload=None, source_ids=["RB-016"], ok=True,
                              error=None, latency_ms=2.0)
    assert (a.evidence_id, b.evidence_id) == ("EVD-000001", "EVD-000002")
    assert (a.sequence, b.sequence) == (1, 2)
    assert reg.ids() == ["EVD-000001", "EVD-000002"]
    assert len(reg) == 2 and "EVD-000002" in reg and "EVD-000003" not in reg
    assert a.acquired_at == FIXED
    assert a.result_kind == "deployments" and b.result_kind == "document"
    assert reg.find_by_record("RB-016") == [b]
    assert reg.all_record_ids() == {"DEP-0001", "DEP-0002", "RB-016"}


def test_content_hash_is_canonical():
    assert canonical_json({"b": 1, "a": [2, 1]}) == '{"a":[2,1],"b":1}'
    assert content_hash({"b": 1, "a": 2}, None) == content_hash({"a": 2, "b": 1}, None)
    assert content_hash({"a": 1}, None) != content_hash({"a": 2}, None)
    assert content_hash(None, "x") != content_hash(None, "y")
    assert content_hash({}, None).startswith("sha256:") and len(content_hash({}, None)) == 71


def test_failed_results_carry_no_source_ids():
    reg = EvidenceRegistry("inv-1")
    item = reg.register_tool_result(name="query_logs", arguments={"node": "x"}, payload=None,
                                    text="Error executing tool", ok=False, error="Unknown node", latency_ms=0.5)
    assert item.ok is False and item.source_ids == [] and item.result_kind is None
    assert item.error == "Unknown node"


def test_registry_requires_investigation_id():
    with pytest.raises(ValueError):
        EvidenceRegistry("")


@pytest.mark.parametrize(("raw", "evidence_id", "record_id"), [
    ("EVD-000004", "EVD-000004", None),
    ("EVD-000004#DEP-0142", "EVD-000004", "DEP-0142"),
    ("  EVD-000010#LOG-checkout-20260803T0810-017 ", "EVD-000010", "LOG-checkout-20260803T0810-017"),
    ("EVD-000001#MET-checkout-latency_p95-dependency:inventory-20260803T0544-20260803T0844-300", "EVD-000001",
     "MET-checkout-latency_p95-dependency:inventory-20260803T0544-20260803T0844-300"),
])
def test_parse_citation_accepts_grammar(raw, evidence_id, record_id):
    citation = parse_citation(raw)
    assert citation is not None
    assert (citation.evidence_id, citation.record_id) == (evidence_id, record_id)
    assert str(citation) == raw.strip()


@pytest.mark.parametrize("raw", ["EVD-4", "evd-000004", "EVD-0000041", "EVD-000004#", "EVD-000004##DEP", "DEP-0142",
                                 "EVD-000004 DEP-0142", "EVD-000004#DEP 0142", ""])
def test_parse_citation_rejects_malformed(raw):
    assert parse_citation(raw) is None


def test_validator_codes():
    reg = EvidenceRegistry("inv-A")
    good = reg.register_tool_result(name="get_deployments", arguments={}, payload=_payload(), text="{}", ok=True,
                                    error=None, latency_ms=1.0)
    failed = reg.register_tool_result(name="query_logs", arguments={}, payload=None, text="err", ok=False,
                                      error="boom", latency_ms=1.0)
    v = CitationValidator(reg)
    assert v.check(good.evidence_id).code == "ok"
    assert v.check(f"{good.evidence_id}#DEP-0002").code == "ok"
    assert v.check(f"{good.evidence_id}#DEP-9999").code == "unknown_record"
    assert v.check("EVD-000099").code == "unknown_evidence"           # never gathered
    assert v.check("EVD-1").code == "malformed"
    assert v.check("the deployment caused it").code == "malformed"
    err = v.check(failed.evidence_id)
    assert err.code == "error_evidence" and err.ok and err.warning
    results = v.check_many([good.evidence_id, "EVD-000099"])
    assert [r.code for r in results] == ["ok", "unknown_evidence"]


def test_cross_investigation_citations_are_rejected():
    reg_a = EvidenceRegistry("inv-A")
    reg_b = EvidenceRegistry("inv-B")
    a1 = reg_a.register_tool_result(name="get_alerts", arguments={}, payload=_payload("alerts", ["ALT-0001"]),
                                    text="{}", ok=True, error=None, latency_ms=1.0)
    b1 = reg_b.register_tool_result(name="get_alerts", arguments={}, payload=_payload("alerts", ["ALT-0077"]),
                                    text="{}", ok=True, error=None, latency_ms=1.0)
    assert a1.evidence_id == b1.evidence_id == "EVD-000001"  # ids are investigation-local by design
    validator_b = CitationValidator(reg_b)
    # a string citation resolves against B's registry: it exists there, but the *record* from A does not
    assert validator_b.check("EVD-000001#ALT-0001").code == "unknown_record"
    assert validator_b.check("EVD-000001#ALT-0077").code == "ok"
    # an evidence object from A presented to B is rejected outright
    assert validator_b.check_item(a1).code == "foreign_investigation"
    assert validator_b.check_item(b1).code == "ok"
    # an item with a matching id but different content is not B's item either
    tampered = b1.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    assert validator_b.check_item(tampered).code == "unknown_evidence"


def test_extract_citations_from_free_text():
    text = "Latency rose (EVD-000002) after DEP-0083 shipped [EVD-000001#DEP-0083]; see EVD-000002 again and EVD-1."
    assert extract_citations(text) == ["EVD-000002", "EVD-000001#DEP-0083"]
