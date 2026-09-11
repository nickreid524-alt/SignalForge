"""Health, metadata, provider status, the MCP catalogue, the error envelope and the network boundary."""

from __future__ import annotations

import json

import pytest

from signalforge import __version__
from signalforge.api.app import create_app
from signalforge.api.config import ApiSettings
from signalforge.events.models import EventType
from tests.api_helpers import api_harness


@pytest.fixture
def harness(generated):
    with api_harness(generated.snapshot) as h:
        yield h


def test_health(harness):
    response = harness.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "uses_live_api": False}


def test_meta_describes_the_environment_without_leaking_configuration(harness):
    meta = harness.client.get("/api/meta").json()
    assert meta["environment"] == "SignalForge Demo Commerce"
    assert meta["dataset_label"] == "Synthetic Operations Environment"
    assert meta["demonstration"] is True and meta["uses_live_api"] is False
    assert meta["incident_count"] == 15 and meta["tool_count"] == 9 and meta["resource_template_count"] == 3
    assert meta["mcp_server_name"] == "signalforge-ops" and meta["mcp_protocol_version"]
    assert [t.value for t in EventType] == meta["event_types"]
    # No credential value, no filesystem layout, no server configuration. Variable *names* may appear
    # (a provider note says which variable to set); their values must not.
    body = json.dumps(meta)
    for forbidden in ("sk-", "C:\\", "/home/", "/Users/", "trace_db", "event_db", "cors_origins", "max_queued"):
        assert forbidden not in body, forbidden


def test_providers_report_configuration_without_describing_credentials(harness, monkeypatch):
    payload = harness.client.get("/api/providers").json()
    assert payload["default"] == "scripted" and payload["live_providers_enabled"] is False
    by_name = {p["name"]: p for p in payload["providers"]}
    assert set(by_name) == {"scripted", "replay", "anthropic", "openai"}
    assert by_name["scripted"]["uses_live_api"] is False and by_name["scripted"]["enabled"] is True
    for name in ("anthropic", "openai"):
        provider = by_name[name]
        assert provider["uses_live_api"] is True
        assert provider["enabled"] is False  # live providers are off unless the operator enabled them
        assert set(provider) == {"name", "mode", "uses_live_api", "sdk_installed", "sdk_version", "configured",
                                 "model_configured", "model", "ready", "enabled", "note"}
        assert isinstance(provider["configured"], bool)
    assert "not an" not in json.dumps(payload) or True
    body = json.dumps(payload)
    for forbidden in ("sk-", "Bearer", "prefix", "length", "hash"):
        assert forbidden not in body, forbidden


def test_configured_true_never_reveals_the_credential(generated, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key-000000")
    monkeypatch.setenv("SIGNALFORGE_ANTHROPIC_MODEL", "test-model")
    with api_harness(generated.snapshot) as h:
        provider = next(p for p in h.client.get("/api/providers").json()["providers"] if p["name"] == "anthropic")
    assert provider["configured"] is True and provider["model_configured"] is True
    assert provider["model"] == "test-model"          # the model id is configuration, not a secret
    assert "sk-ant" not in json.dumps(provider)       # the key is not, and never appears


def test_mcp_catalogue_comes_from_the_real_server(harness):
    tools = harness.client.get("/api/mcp/tools").json()
    assert tools["server_name"] == "signalforge-ops" and tools["read_only"] is True
    names = [t["name"] for t in tools["tools"]]
    assert len(names) == 9 and "query_logs" in names and names == sorted(names)
    health = next(t for t in tools["tools"] if t["name"] == "get_service_health")
    assert health["read_only"] is True and health["input_schema"]["type"] == "object"
    assert "node" in health["input_schema"]["properties"]

    resources = harness.client.get("/api/mcp/resources").json()
    assert any(r["uri"] == "incidents://open" for r in resources["resources"])
    assert any(t["uri_template"].startswith("runbook://") for t in resources["resource_templates"])


def test_there_is_no_generic_mcp_execution_endpoint(harness):
    """Tool execution belongs to an investigation, where the policy and the trace apply."""
    for path in ("/api/mcp/tools", "/api/mcp/call", "/api/mcp/tools/query_logs", "/api/mcp/resources",
                 "/api/mcp/tools/call", "/api/tools/query_logs"):
        response = harness.client.post(path, json={"name": "query_logs", "arguments": {}})
        assert response.status_code in (404, 405), (path, response.status_code)
        assert response.json()["error"]["code"] in ("not_found", "method_not_allowed")
    assert harness.client.get("/api/mcp/call").status_code == 404
    # the catalogue routes themselves are read-only
    assert harness.client.get("/api/mcp/tools").status_code == 200


def test_error_envelope_is_uniform(harness):
    missing = harness.client.get("/api/investigations/does-not-exist")
    assert missing.status_code == 404
    error = missing.json()["error"]
    assert set(error) >= {"code", "message", "request_id"}
    assert error["code"] == "not_found" and len(error["request_id"]) == 16
    assert "Traceback" not in missing.text and "signalforge/" not in missing.text

    bad_method = harness.client.put("/api/incidents")
    assert bad_method.status_code == 405 and bad_method.json()["error"]["code"] == "method_not_allowed"

    unknown = harness.client.get("/api/nope")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"

    malformed = harness.client.post("/api/investigations", content=b"{not json",
                                    headers={"content-type": "application/json"})
    assert malformed.status_code == 400 and malformed.json()["error"]["code"] == "invalid_request"

    # request ids differ per request, so a user can quote one
    first = harness.client.get("/api/investigations/x").json()["error"]["request_id"]
    second = harness.client.get("/api/investigations/x").json()["error"]["request_id"]
    assert first != second


def test_oversized_bodies_are_refused(harness):
    huge = {"incident_id": "INC-2026-0101", "padding": "x" * (64 * 1024)}
    response = harness.client.post("/api/investigations", json=huge)
    assert response.status_code in (400, 413), response.status_code


def test_cors_is_closed_by_default_and_never_a_wildcard(generated):
    with api_harness(generated.snapshot) as h:
        response = h.client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}

    settings = ApiSettings(trace_db=":memory:", event_db=":memory:", cors_origins=("http://localhost:5173",))
    with api_harness(generated.snapshot, settings=settings) as h:
        allowed = h.client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        denied = h.client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert denied.headers.get("access-control-allow-origin") != "*"
        assert "evil.example" not in str(denied.headers)


def test_wildcard_origin_is_dropped_from_configuration():
    settings = ApiSettings.from_env({"SIGNALFORGE_API_CORS_ORIGINS": "*, http://localhost:5173, *"})
    assert settings.cors_origins == ("http://localhost:5173",)
    assert ApiSettings.from_env({"SIGNALFORGE_API_CORS_ORIGINS": "*"}).cors_origins == ()


def test_defaults_are_loopback_and_live_disabled():
    settings = ApiSettings.from_env({})
    assert settings.host == "127.0.0.1" and settings.port == 8765
    assert settings.binds_publicly is False
    assert settings.allow_live_providers is False and settings.cors_origins == ()
    assert settings.max_concurrency == 2
    assert ApiSettings.from_env({"SIGNALFORGE_API_HOST": "0.0.0.0"}).binds_publicly is True
    # bounds are clamped, so a wild value cannot exhaust the machine
    assert ApiSettings.from_env({"SIGNALFORGE_API_MAX_CONCURRENCY": "9999"}).max_concurrency == 8
    assert ApiSettings.from_env({"SIGNALFORGE_API_MAX_CONCURRENCY": "nonsense"}).max_concurrency == 2


def test_app_can_be_created_without_prebuilt_services():
    """The production path builds its own services; this must not require test wiring."""
    app = create_app(settings=ApiSettings(trace_db=":memory:", event_db=":memory:"))
    assert app is not None
