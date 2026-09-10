"""MCP server contracts, exercised through the official client over the in-memory transport."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import jsonschema
import pytest

from signalforge.config import DATA_NOTICE
from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.tools import TOOL_NAMES

pytestmark = pytest.mark.anyio

CLOCK = datetime(2026, 8, 3, 8, 44, tzinfo=UTC)
START = (CLOCK - timedelta(hours=3)).isoformat()
END = CLOCK.isoformat()


async def test_tools_list_matches_catalogue(client: OpsClient):
    tools = await client.list_tools()
    assert [t.name for t in tools] == sorted(TOOL_NAMES)
    for tool in tools:
        assert tool.read_only is True, tool.name
        assert tool.idempotent is True, tool.name
        assert tool.description
        assert tool.input_schema.get("type") == "object"
        assert tool.output_schema and tool.output_schema.get("type") == "object"
        props = tool.output_schema["properties"]
        for required in ("kind", "source_ids", "query", "summary", "data_notice"):
            assert required in props, (tool.name, required)


async def test_server_identity(client: OpsClient):
    assert client.server_name == "signalforge-ops"
    assert client.server_version == "0.1.0"
    assert client.protocol_version
    assert "read-only" in (client.instructions or "").lower()


SUCCESS_CALLS = [
    ("get_service_health", {"node": "checkout", "as_of": END}),
    ("query_metrics", {"node": "checkout", "metric": "latency_p95", "start": START, "end": END}),
    ("query_metrics", {"node": "checkout", "metric": "dependency_latency_p95", "start": START, "end": END,
                       "dimension": "dependency=inventory", "step_seconds": 900}),
    ("query_logs", {"node": "checkout", "start": START, "end": END, "level": "WARN", "limit": 20}),
    ("get_deployments", {"service": "checkout", "start": START, "end": END}),
    ("get_deployments", {"start": START, "end": END}),
    ("get_config_changes", {"node": "reporting", "start": "2026-08-05T10:00:00Z", "end": "2026-08-05T14:32:00Z"}),
    ("get_alerts", {"start": START, "end": END, "min_severity": "SEV2"}),
    ("get_dependencies", {"service": "checkout", "direction": "downstream"}),
    ("search_runbooks", {"query": "checkout latency dependency slow", "service": "checkout", "limit": 3}),
    ("search_incidents", {"query": "payvault certificate expired midnight", "limit": 3}),
]


@pytest.mark.parametrize(("name", "arguments"), SUCCESS_CALLS, ids=[f"{n}:{i}" for i, (n, _) in enumerate(SUCCESS_CALLS)])
async def test_tool_success_and_schema_conformance(client: OpsClient, name: str, arguments: dict):
    schema = next(t.output_schema for t in await client.list_tools() if t.name == name)
    outcome = await client.call_tool(name, arguments)
    assert outcome.ok, outcome.error
    assert outcome.payload is not None
    jsonschema.validate(outcome.payload, schema)  # structured_content honours the published output_schema
    assert outcome.payload["data_notice"] == DATA_NOTICE
    assert isinstance(outcome.payload["source_ids"], list)
    assert outcome.payload["summary"]
    assert outcome.text  # the text channel carries the same result for models without structured support
    assert json.loads(outcome.text)["kind"] == outcome.payload["kind"]


async def test_health_result_contents(client: OpsClient):
    out = await client.call_tool("get_service_health", {"node": "checkout", "as_of": END})
    health = out.payload["health"]
    assert health["status"] in ("degraded", "critical")
    assert out.payload["source_ids"][0].startswith("HLT-checkout-")
    assert set(health["open_alert_ids"]) <= set(out.payload["source_ids"])
    external = await client.call_tool("get_service_health", {"node": "payvault", "as_of": "2026-09-02T12:18:00Z"})
    assert external.payload["health"]["status_feed"] == "major_outage"


async def test_metric_result_contents(client: OpsClient):
    out = await client.call_tool("query_metrics", {"node": "checkout", "metric": "latency_p95", "start": START, "end": END})
    p = out.payload
    assert p["unit"] == "ms" and p["step_seconds"] == 300 and len(p["points"]) == 36
    assert p["source_ids"] == [p["series_id"]]
    assert p["series_id"].startswith("MET-checkout-latency_p95-")
    assert p["stats"]["largest_step_ratio"] > 5
    assert p["stats"]["largest_step_at"].startswith("2026-08-03T08:1")


async def test_log_result_contents_and_size_limit(client: OpsClient):
    out = await client.call_tool("query_logs", {"node": "checkout", "start": START, "end": END, "limit": 7})
    p = out.payload
    assert p["returned"] == 7 and len(p["entries"]) == 7 and p["truncated"] is True
    assert p["total_matches"] > 7
    assert p["source_ids"] == [e["id"] for e in p["entries"]]
    assert p["top_patterns"][0]["count"] >= p["top_patterns"][-1]["count"]
    assert any(t["pattern_id"] == "checkout.reserve_fanout" for t in p["top_patterns"])
    filtered = await client.call_tool("query_logs", {"node": "checkout", "start": START, "end": END,
                                                     "contains": "BATCH_MODE=FALSE", "limit": 500})
    assert filtered.payload["total_matches"] > 0
    assert all("batch_mode=false" in e["message"] for e in filtered.payload["entries"])


async def test_deployments_and_config_contents(client: OpsClient):
    deps = await client.call_tool("get_deployments", {"service": "checkout", "start": START, "end": END})
    assert len(deps.payload["deployments"]) == 1
    dep = deps.payload["deployments"][0]
    assert dep["version"] == "v2026.08.03-1" and dep["id"].startswith("DEP-")
    assert deps.payload["source_ids"] == [dep["id"]]
    cfg = await client.call_tool("get_config_changes", {"node": "reporting", "start": "2026-08-05T10:00:00Z",
                                                        "end": "2026-08-05T14:32:00Z"})
    keys = [c["key"] for c in cfg.payload["changes"]]
    assert "reporting.db_host" in keys


async def test_dependencies_contents(client: OpsClient):
    out = await client.call_tool("get_dependencies", {"service": "checkout"})
    ids = {e["id"] for e in out.payload["edges"]}
    assert "EDGE-checkout-pricing" in ids and "EDGE-edge-gateway-checkout" in ids
    assert out.payload["topology_resource_uri"] == "topology://services/checkout"
    assert out.payload["source_ids"][0] == "checkout"


async def test_search_results_return_snippets_and_uris_not_documents(client: OpsClient):
    out = await client.call_tool("search_runbooks", {"query": "payvault client certificate expired handshake"})
    hits = out.payload["hits"]
    assert hits and hits[0]["source_id"] == "RB-016"
    assert hits[0]["resource_uri"] == "runbook://RB-016"
    assert len(hits[0]["snippet"]) < 400
    assert out.payload["strategy"] == "lexical-fts5-bm25"
    inc = await client.call_tool("search_incidents", {"query": "certificate expired midnight payments"})
    assert inc.payload["hits"][0]["source_id"] == "INC-2025-0031"
    assert inc.payload["hits"][0]["category"] == "certificate_expiry"


@pytest.mark.parametrize(("name", "arguments", "fragment"), [
    ("get_service_health", {"node": "Checkout", "as_of": END}, "pattern"),                 # schema: id pattern
    ("get_service_health", {"node": "warehouse-2", "as_of": END}, "Unknown node"),          # tool: unknown node
    ("query_logs", {"node": "checkout", "start": START, "end": END, "limit": 501}, "less than or equal"),
    ("query_logs", {"node": "checkout", "start": START, "end": "2026-08-07T00:00:00Z"}, "too large"),
    ("query_logs", {"node": "checkout", "start": END, "end": START}, "after start"),
    ("query_metrics", {"node": "checkout", "metric": "latency_p95", "start": START, "end": END, "step_seconds": 60,
                       }, "Too many points") if False else
    ("query_metrics", {"node": "checkout", "metric": "latency_p95", "start": "2026-08-01T00:00:00Z",
                       "end": "2026-08-03T00:00:00Z", "step_seconds": 60}, "Too many points"),
    ("query_metrics", {"node": "checkout", "metric": "dependency_latency_p95", "start": START, "end": END},
     "requires a dimension"),
    ("query_metrics", {"node": "payvault", "metric": "error_rate", "start": START, "end": END}, "external dependency"),
    ("query_metrics", {"node": "checkout", "metric": "not_a_metric", "start": START, "end": END}, "not_a_metric"),
    ("query_logs", {"node": "checkout", "start": START, "end": END, "contains": "x" * 121}, "at most 120"),
    ("search_runbooks", {"query": "cache", "limit": 11}, "less than or equal"),
    ("get_alerts", {"start": START, "end": END, "min_severity": "SEV9"}, "SEV9"),
    ("get_dependencies", {"service": "nowhere"}, "Unknown node"),
])
async def test_argument_validation_and_tool_errors_are_results_not_exceptions(client: OpsClient, name, arguments, fragment):
    outcome = await client.call_tool(name, arguments)
    assert outcome.ok is False
    assert outcome.error_kind == "tool_error"
    assert fragment.lower() in (outcome.error or "").lower(), outcome.error
    assert outcome.payload is None


async def test_unknown_tool_is_a_normalised_failure(client: OpsClient):
    outcome = await client.call_tool("restart_service", {"node": "checkout"})
    assert outcome.ok is False and outcome.error_kind == "tool_error"
    assert "restart_service" in (outcome.error or "")


async def test_resources_list(client: OpsClient):
    statics = await client.list_resources()
    assert [r.uri for r in statics] == ["catalog://services"]
    templates = await client.list_resource_templates()
    assert [t.uri_template for t in templates] == sorted(
        ["topology://services/{service}", "runbook://{runbook_id}", "incident://{incident_id}"])


async def test_read_resources(client: OpsClient):
    catalog = await client.read_resource("catalog://services")
    assert catalog.ok and catalog.mime_type == "application/json"
    assert len(catalog.payload["services"]) == 12 and "checkout" in catalog.source_ids

    topo = await client.read_resource("topology://services/checkout")
    assert topo.ok and {e["target"] for e in topo.payload["downstream"]} >= {"pricing", "inventory", "payments"}
    assert "EDGE-checkout-pricing" in topo.source_ids

    rb = await client.read_resource("runbook://RB-016")
    assert rb.ok and rb.mime_type == "text/markdown" and rb.text.startswith("# Rotating the PayVault client certificate")
    assert rb.source_ids == ["RB-016"] and DATA_NOTICE in rb.text

    past = await client.read_resource("incident://INC-2025-0031")
    assert past.ok and "rca_summary" in past.text
    current = await client.read_resource("incident://INC-2026-0101")
    assert current.ok and "status: open" in current.text and "rca_summary" not in current.text


@pytest.mark.parametrize("uri", ["runbook://RB-999", "incident://INC-1999-0001", "topology://services/nowhere",
                                 "runbook://../../etc/passwd"])
async def test_missing_resources_are_protocol_errors_normalised_by_the_client(client: OpsClient, uri: str):
    doc = await client.read_resource(uri)
    assert doc.ok is False
    assert doc.error and ("protocol_error" in doc.error or "transport" in doc.error)
    assert doc.text is None and doc.source_ids == []
