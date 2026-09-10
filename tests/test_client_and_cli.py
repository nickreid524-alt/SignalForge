"""OpsClient normalisation and evidence integration; developer CLI."""

from __future__ import annotations

import pytest

from signalforge import cli
from signalforge.evidence.registry import EvidenceRegistry
from signalforge.mcp_client.client import OpsClient, source_ids_from_uri

END = "2026-08-03T08:44:00Z"


@pytest.mark.anyio
async def test_gather_registers_success_with_world_ids(client: OpsClient):
    registry = EvidenceRegistry("inv-test")
    item = await client.gather(registry, "get_deployments", {"service": "checkout", "start": "2026-08-03T05:44:00Z",
                                                            "end": END})
    assert item.evidence_id == "EVD-000001" and item.ok and item.source_kind == "tool"
    assert item.source_ids and all(s.startswith("DEP-") for s in item.source_ids)
    assert item.payload["kind"] == "deployments" and item.result_kind == "deployments"
    assert item.latency_ms >= 0
    assert item.arguments["service"] == "checkout"


@pytest.mark.anyio
async def test_gather_registers_failures_as_failed_evidence(client: OpsClient):
    registry = EvidenceRegistry("inv-test")
    item = await client.gather(registry, "get_service_health", {"node": "ghost", "as_of": END})
    assert item.evidence_id == "EVD-000001" and item.ok is False
    assert item.source_ids == [] and item.payload is None
    assert "Unknown node" in (item.error or "")
    unknown = await client.gather(registry, "no_such_tool", {})
    assert unknown.evidence_id == "EVD-000002" and unknown.ok is False


@pytest.mark.anyio
async def test_read_as_evidence(client: OpsClient):
    registry = EvidenceRegistry("inv-test")
    doc = await client.read_as_evidence(registry, "runbook://RB-016")
    assert doc.ok and doc.source_kind == "resource" and doc.source_ids == ["RB-016"] and doc.result_kind == "document"
    topo = await client.read_as_evidence(registry, "topology://services/payments")
    assert "EDGE-payments-payvault" in topo.source_ids
    missing = await client.read_as_evidence(registry, "runbook://RB-000")
    assert missing.ok is False and missing.source_ids == [] and missing.error


def test_source_ids_from_uri():
    assert source_ids_from_uri("runbook://RB-016", None) == ["RB-016"]
    assert source_ids_from_uri("incident://INC-2025-0031", None) == ["INC-2025-0031"]
    assert source_ids_from_uri("catalog://services", {"source_ids": ["a", "b", 3]}) == ["a", "b"]
    assert source_ids_from_uri("weird://thing", None) == ["weird://thing"]


def test_no_sdk_types_leak_from_client_api():
    import inspect

    from signalforge.mcp_client import client as module

    for name in ("ToolDescriptor", "ResourceDescriptor", "ResourceTemplateDescriptor", "ResourceDocument",
                 "ToolCallOutcome"):
        cls = getattr(module, name)
        for field_type in inspect.get_annotations(cls).values():
            assert "mcp." not in str(field_type), (name, field_type)


def test_cli_world_stats(capsys):
    assert cli.main(["world", "stats"]) == 0
    out = capsys.readouterr().out
    assert "SignalForge Demo Commerce" in out and "INC-2026-0115" in out


def test_cli_scenarios_list(capsys):
    assert cli.main(["scenarios", "list"]) == 0
    assert "SCN-15" in capsys.readouterr().out


def test_cli_world_export(tmp_path, capsys):
    out = tmp_path / "world.json"
    assert cli.main(["world", "export", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert '"environment": "SignalForge Demo Commerce"' in text and "manifest" not in text


def test_cli_demo_in_memory(capsys):
    assert cli.main(["demo", "--incident", "INC-2026-0106"]) == 0
    out = capsys.readouterr().out
    assert "ENGINEERING DEMONSTRATION" in out
    assert "protocol=" in out and "EVD-000001" in out and "EVD-000007" in out
    assert "search_runbooks" in out and "runbook://" in out
    assert "ok=True" in out


def test_cli_demo_unknown_incident(capsys):
    assert cli.main(["demo", "--incident", "INC-0000-0000"]) == 2
