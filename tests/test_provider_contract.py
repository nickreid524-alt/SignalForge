"""Provider-contract parity: scripted, replay, and the Anthropic/OpenAI adapters (over fake vendor servers)
produce equivalent neutral objects and equivalent investigations."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from signalforge.audit.store import TraceStore
from signalforge.mcp_client.client import OpsClient
from signalforge.orchestration.investigator import Investigator
from signalforge.orchestration.state import InvestigationStatus as S
from signalforge.providers.anthropic_provider import AnthropicProvider, AnthropicSettings
from signalforge.providers.base import (
    GenerationConfig,
    InvestigationContext,
    SystemPrompt,
    ToolSpec,
    UserMessage,
)
from signalforge.providers.openai_provider import OpenAIProvider, OpenAISettings
from signalforge.providers.replay import Cassette, RecordingProvider, ReplayProvider
from signalforge.providers.scripted import ScriptedDemoProvider
from signalforge.reports.schema import ReportDraft
from tests.helpers import valid_draft
from tests.vendor_fakes import (
    FakeAnthropicClient,
    FakeOpenAIClient,
    FixedResponder,
    ScriptedAnthropicResponder,
    ScriptedOpenAIResponder,
    ant_message,
    ant_text,
    ant_tool_use,
    oa_function_call,
    oa_message,
    oa_response,
    oa_text,
)

pytestmark = pytest.mark.anyio

INCIDENT = "INC-2026-0106"
CONTEXT = InvestigationContext(investigation_id="c", incident_id=INCIDENT, affected_service="payments",
                               investigation_clock=datetime(2026, 8, 14, 0, 28, tzinfo=UTC), step=1, purpose="deliberate")
TOOLS = [ToolSpec(name="get_service_health", description="h", input_schema={"type": "object"})]


def _report_key(report):
    return report.model_dump(exclude={"provider", "investigation_duration_seconds", "token_usage", "investigation_id"})


async def _run(server, provider, investigation_id: str):
    trace = TraceStore(":memory:")
    async with OpsClient.in_memory(server) as client:
        result = await Investigator(provider, client, trace=trace).run(INCIDENT, investigation_id=investigation_id)
    return result, trace


async def test_same_logical_vendor_turn_yields_identical_neutral_turns():
    anthropic = AnthropicProvider(AnthropicSettings(model="m"), client=FakeAnthropicClient(FixedResponder([
        ant_message([ant_text("Checking health."), ant_tool_use("shared-id", "get_service_health", {"node": "payments"})], stop_reason="tool_use")])))
    openai = OpenAIProvider(OpenAISettings(model="m"), client=FakeOpenAIClient(FixedResponder([
        oa_response([oa_message([oa_text("Checking health.")]), oa_function_call("shared-id", "get_service_health", {"node": "payments"})])])))
    conv = [UserMessage(text="seed")]
    a = await anthropic.complete(SystemPrompt(text="s"), conv, tools=TOOLS, context=CONTEXT, config=GenerationConfig())
    o = await openai.complete(SystemPrompt(text="s"), conv, tools=TOOLS, context=CONTEXT, config=GenerationConfig())
    assert a.model_dump(exclude={"opaque", "usage", "latency_ms"}) == o.model_dump(exclude={"opaque", "usage", "latency_ms"})
    assert a.usage.reported and o.usage.reported


async def test_same_logical_structured_output_yields_identical_drafts():
    draft = ReportDraft.model_validate(valid_draft())
    anthropic = AnthropicProvider(AnthropicSettings(model="m"), client=FakeAnthropicClient(FixedResponder([
        ant_message([ant_text(draft.model_dump_json())], parsed_output=draft)])))
    openai = OpenAIProvider(OpenAISettings(model="m"), client=FakeOpenAIClient(FixedResponder([
        oa_response([oa_message([oa_text(draft.model_dump_json())])], output_parsed=draft, output_text=draft.model_dump_json())])))
    conv = [UserMessage(text="seed")]
    a = await anthropic.generate_structured(SystemPrompt(text="s"), conv, schema=ReportDraft, context=CONTEXT, config=GenerationConfig())
    o = await openai.generate_structured(SystemPrompt(text="s"), conv, schema=ReportDraft, context=CONTEXT, config=GenerationConfig())
    assert a.value == o.value == draft and a.raw == o.raw


async def test_full_investigation_parity_across_all_provider_kinds(server, tmp_path):
    scripted_result, _ = await _run(server, ScriptedDemoProvider(), "parity-scripted")
    assert scripted_result.state.status is S.COMPLETED

    recorder = RecordingProvider(ScriptedDemoProvider())
    recorded_result, _ = await _run(server, recorder, "parity-recording")
    cassette_path = recorder.cassette.save(tmp_path / "parity.json")
    replay_result, _ = await _run(server, ReplayProvider.from_file(cassette_path), "parity-replay")

    anthropic = AnthropicProvider(AnthropicSettings(model="fake-claude"), client=FakeAnthropicClient(ScriptedAnthropicResponder()))
    anthropic_result, anthropic_trace = await _run(server, anthropic, "parity-anthropic")
    openai = OpenAIProvider(OpenAISettings(model="fake-gpt"), client=FakeOpenAIClient(ScriptedOpenAIResponder()))
    openai_result, openai_trace = await _run(server, openai, "parity-openai")

    for result in (recorded_result, replay_result, anthropic_result, openai_result):
        assert result.state.status is S.COMPLETED, (result.state.status, result.state.error)
        assert _report_key(result.report) == _report_key(scripted_result.report)
        assert result.registry.ids() == scripted_result.registry.ids()
        calls = [(a.name, a.arguments) for step in result.state.steps for a in step.actions if a.kind == "call_tool"]
        expected = [(a.name, a.arguments) for step in scripted_result.state.steps for a in step.actions if a.kind == "call_tool"]
        assert calls == expected
        assert [(h.id, h.status, h.confidence) for h in result.state.hypotheses.hypotheses] == \
               [(h.id, h.status, h.confidence) for h in scripted_result.state.hypotheses.hypotheses]

    # token telemetry: none for scripted/replay, real numbers for the (fake) live adapters
    assert not scripted_result.report.token_usage.reported and not replay_result.report.token_usage.reported
    assert anthropic_result.report.token_usage.reported and anthropic_result.report.token_usage.input_tokens > 0
    assert anthropic_result.report.token_usage.cached_input_tokens > 0
    assert openai_result.report.token_usage.reported and openai_result.report.token_usage.reasoning_output_tokens > 0
    calls = anthropic_trace.load("parity-anthropic")["model_calls"]
    assert all(c["usage_reported"] == 1 and c["input_tokens"] > 0 and c["provider_model"] == "fake-claude" for c in calls)
    assert all(c["error_category"] is None for c in calls)
    scripted_calls = TraceStore(":memory:")  # scripted usage columns are null, not zero
    del scripted_calls
    # opaque provider blocks were exchanged with the fake vendors but never persisted
    for trace, inv in ((anthropic_trace, "parity-anthropic"), (openai_trace, "parity-openai")):
        dumped = json.dumps(trace.load(inv), default=str)
        assert "sig-opaque" not in dumped and "enc-opaque" not in dumped and "should not persist" not in dumped
        assert "opaque" not in json.dumps([c["response"] for c in trace.load(inv)["model_calls"]])


async def test_cassette_v2_contract(server, tmp_path):
    recorder = RecordingProvider(AnthropicProvider(AnthropicSettings(model="fake-claude"),
                                                   client=FakeAnthropicClient(ScriptedAnthropicResponder())))
    result, _ = await _run(server, recorder, "cassette-v2")
    assert result.state.status is S.COMPLETED
    path = recorder.cassette.save(tmp_path / "live-like.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2 and raw["provider"]["name"] == "anthropic" and raw["model"] == "fake-claude" and raw["sanitized"] is True
    entry = raw["entries"][0]
    assert set(entry) >= {"index", "purpose", "fingerprint", "kind", "request", "turn", "usage", "latency_ms"}
    assert entry["request"]["system"].startswith("You are SignalForge") and entry["request"]["tool_names"]
    assert entry["turn"]["opaque"] == [] and entry["usage"]["reported"] is True
    text = path.read_text(encoding="utf-8")
    for forbidden in ("sig-opaque", "api_key", "Authorization", "x-api-key", "encrypted_content"):
        assert forbidden not in text, forbidden
    assert all(m.get("opaque") in (None, []) for e in raw["entries"] for m in e["request"]["conversation"] if m["role"] == "assistant")
    # the cassette replays the live-like run exactly and reports the recorded token usage
    replayed, _ = await _run(server, ReplayProvider(Cassette.load(path)), "cassette-v2-replay")
    assert replayed.state.status is S.COMPLETED and _report_key(replayed.report) == _report_key(result.report)
    assert replayed.report.token_usage.reported and replayed.report.token_usage.input_tokens == result.report.token_usage.input_tokens
