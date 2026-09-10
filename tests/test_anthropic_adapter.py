"""AnthropicProvider: mapping, parsing, structured output, errors, setup failures. No network."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from signalforge.providers.anthropic_provider import AnthropicProvider, AnthropicSettings
from signalforge.providers.base import (
    AssistantMessage,
    GenerationConfig,
    InvestigationContext,
    SystemPrompt,
    ToolRequest,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from signalforge.providers.errors import ProviderFailure, classify_exception
from signalforge.reports.schema import ReportDraft
from tests.helpers import valid_draft
from tests.vendor_fakes import (
    FakeAnthropicClient,
    FixedResponder,
    ant_message,
    ant_text,
    ant_thinking,
    ant_tool_use,
    ant_usage,
)

pytestmark = pytest.mark.anyio

SETTINGS = AnthropicSettings(model="test-claude-model", effort="high")
CONTEXT = InvestigationContext(investigation_id="inv-a", incident_id="INC-2026-0101", affected_service="checkout",
                               investigation_clock=datetime(2026, 8, 3, 8, 44, tzinfo=UTC), step=1, purpose="deliberate")
CONFIG = GenerationConfig(max_output_tokens=2048, timeout_seconds=30)
TOOLS = [ToolSpec(name="get_service_health", description="health", input_schema={"type": "object", "properties": {"node": {"type": "string"}}, "required": ["node"]})]
CONVERSATION = [
    UserMessage(text="seed"),
    UserMessage(text="status block"),
    AssistantMessage(text="checking", tool_requests=[ToolRequest(id="tu_1", name="get_service_health", arguments={"node": "checkout"})],
                     opaque=[{"type": "thinking", "signature": "s", "thinking": ""}]),
    ToolResultsMessage(results=[ToolResult(request_id="tu_1", content="evidence_id: EVD-000003", is_error=False)]),
    UserMessage(text="next status"),
]


def _provider(responses: list) -> tuple[AnthropicProvider, FakeAnthropicClient]:
    client = FakeAnthropicClient(FixedResponder(responses))
    return AnthropicProvider(SETTINGS, client=client), client


def test_message_conversion_merges_same_role_turns_and_keeps_alternation():
    messages = AnthropicProvider.to_messages(CONVERSATION)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == [{"type": "text", "text": "seed"}, {"type": "text", "text": "status block"}]
    assistant = messages[1]["content"]
    assert assistant[0] == {"type": "thinking", "signature": "s", "thinking": ""}          # opaque echoed first
    assert assistant[1] == {"type": "text", "text": "checking"}
    assert assistant[2] == {"type": "tool_use", "id": "tu_1", "name": "get_service_health", "input": {"node": "checkout"}}
    assert messages[2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu_1", "content": "evidence_id: EVD-000003", "is_error": False}
    assert messages[2]["content"][1] == {"type": "text", "text": "next status"}
    empty = AnthropicProvider.to_messages([UserMessage(text="x"), AssistantMessage()])
    assert empty[1]["content"] == [{"type": "text", "text": "(no content)"}]


def test_tool_schema_conversion():
    assert AnthropicProvider.to_tools(TOOLS) == [{"name": "get_service_health", "description": "health",
                                                  "input_schema": TOOLS[0].input_schema}]


async def test_complete_parses_text_multiple_tool_uses_thinking_and_usage():
    provider, client = _provider([ant_message(
        [ant_thinking("sig-1"), ant_text("Two checks."), ant_tool_use("tu_a", "get_service_health", {"node": "checkout"}),
         ant_tool_use("tu_b", "query_logs", {"node": "checkout", "limit": 5})],
        stop_reason="tool_use", usage=ant_usage(321, 45, cache_read=100, cache_create=7))])
    turn = await provider.complete(SystemPrompt(text="sys"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert turn.text == "Two checks." and turn.stop_reason == "tool_use"
    assert [(r.id, r.name, r.arguments) for r in turn.tool_requests] == [
        ("tu_a", "get_service_health", {"node": "checkout"}), ("tu_b", "query_logs", {"node": "checkout", "limit": 5})]
    assert turn.opaque == [{"type": "thinking", "signature": "sig-1", "thinking": ""}]
    assert turn.usage.reported and (turn.usage.input_tokens, turn.usage.output_tokens) == (321, 45)
    assert turn.usage.cached_input_tokens == 100 and turn.usage.cache_write_tokens == 7
    kind, request = client.requests[0]
    assert kind == "create" and request["model"] == "test-claude-model" and request["system"] == "sys"
    assert request["tool_choice"] == {"type": "auto"} and request["tools"][0]["name"] == "get_service_health"
    assert request["output_config"] == {"effort": "high"} and request["max_tokens"] >= 2048 and request["timeout"] == 30
    assert "api_key" not in request and "thinking" not in request  # thinking is never configured explicitly
    for module in (type(turn).__module__, type(turn.tool_requests[0]).__module__):
        assert module.startswith("signalforge.")


async def test_refusal_and_context_window_are_normalised():
    provider, _ = _provider([ant_message([ant_text("no")], stop_reason="refusal", stop_details=type("SD", (), {"category": "cyber"})())])
    with pytest.raises(ProviderFailure) as info:
        await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert info.value.category == "refusal" and not info.value.retryable and "cyber" in str(info.value)
    provider, _ = _provider([ant_message([ant_text("")], stop_reason="model_context_window_exceeded")])
    with pytest.raises(ProviderFailure) as info:
        await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert info.value.category == "context_exhausted"


async def test_structured_output_via_parsed_output_and_text_fallback():
    draft = ReportDraft.model_validate(valid_draft())
    provider, client = _provider([ant_message([ant_text(draft.model_dump_json())], usage=ant_usage(10, 20), parsed_output=draft)])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value == draft and result.raw == draft.model_dump(mode="json") and result.usage.reported
    kind, request = client.requests[0]
    assert kind == "parse" and request["output_format"] is ReportDraft and "tools" not in request
    # fallback: no parsed_output but valid JSON text
    provider, _ = _provider([ant_message([ant_text(draft.model_dump_json())])])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value == draft
    # malformed: reported as a parse error, not an exception (the orchestrator repairs)
    provider, _ = _provider([ant_message([ant_text('{"summary": "too short"}')])])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value is None and "did not validate" in (result.parse_error or "")
    provider, _ = _provider([ant_message([])])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value is None and "no structured output" in (result.parse_error or "")


async def test_vendor_exceptions_are_normalised_without_leaking_secrets():
    class RateLimitError(Exception):
        status_code = 429

    class AuthenticationError(Exception):
        status_code = 401

    class NotFoundError(Exception):
        status_code = 404

    class BadRequestError(Exception):
        status_code = 400

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class InternalServerError(Exception):
        status_code = 500

    cases = [
        (RateLimitError("slow down"), "rate_limited", True),
        (AuthenticationError("invalid x-api-key: sk-ant-abcdefghijklmnop123456"), "authentication", False),
        (NotFoundError("model: not-a-model not found"), "invalid_model", False),
        (BadRequestError("prompt is too long: 250000 tokens > 200000 maximum"), "context_exhausted", False),
        (BadRequestError("messages: roles must alternate"), "invalid_request", False),
        (APITimeoutError("Request timed out."), "timeout", True),
        (APIConnectionError("Connection error."), "network", True),
        (InternalServerError("boom"), "server_error", True),
        (RuntimeError("something odd"), "unknown", False),
    ]
    for exc, category, retryable in cases:
        provider, _ = _provider([exc])
        with pytest.raises(ProviderFailure) as info:
            await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
        assert info.value.category == category, (exc, info.value.category)
        assert info.value.retryable is retryable
        assert "sk-ant-" not in str(info.value) and "[REDACTED]" in str(info.value) if "sk-ant" in str(exc) else True


def test_real_sdk_exception_classes_classify_correctly():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(429, request=request)
    assert classify_exception(anthropic.RateLimitError("rate", response=response, body=None), "anthropic").category == "rate_limited"
    response = httpx2.Response(401, request=request)
    assert classify_exception(anthropic.AuthenticationError("auth", response=response, body=None), "anthropic").category == "authentication"
    assert classify_exception(anthropic.APIConnectionError(request=request), "anthropic").category == "network"
    assert classify_exception(anthropic.APITimeoutError(request=request), "anthropic").category == "timeout"


def test_setup_failures_are_clear(monkeypatch):
    with pytest.raises(ProviderFailure) as info:
        AnthropicProvider(AnthropicSettings(model=""), env={})
    assert info.value.category == "missing_model" and "SIGNALFORGE_ANTHROPIC_MODEL" in str(info.value)
    with pytest.raises(ProviderFailure) as info:
        AnthropicProvider(SETTINGS, env={})
    assert info.value.category == "missing_api_key" and "not an API credential" in str(info.value)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # simulate the SDK not being installed
    with pytest.raises(ProviderFailure) as info:
        AnthropicProvider(SETTINGS, env={"ANTHROPIC_API_KEY": "sk-ant-test-not-real-0000000000"})
    assert info.value.category == "missing_sdk" and "signalforge[anthropic]" in str(info.value)
    with pytest.raises(ProviderFailure) as info:
        AnthropicProvider(AnthropicSettings(model="m", effort="turbo"), client=object())
    assert info.value.category == "invalid_request"
