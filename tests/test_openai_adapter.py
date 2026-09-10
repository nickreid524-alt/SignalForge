"""OpenAIProvider: Responses API mapping, parsing, structured output, errors, setup failures. No network."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import pytest

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
from signalforge.providers.openai_provider import OpenAIProvider, OpenAISettings
from signalforge.reports.schema import ReportDraft
from tests.helpers import valid_draft
from tests.vendor_fakes import (
    FakeOpenAIClient,
    FixedResponder,
    oa_function_call,
    oa_message,
    oa_reasoning,
    oa_refusal,
    oa_response,
    oa_text,
    oa_usage,
)

pytestmark = pytest.mark.anyio

SETTINGS = OpenAISettings(model="test-openai-model", reasoning_effort="low")
CONTEXT = InvestigationContext(investigation_id="inv-o", incident_id="INC-2026-0101", affected_service="checkout",
                               investigation_clock=datetime(2026, 8, 3, 8, 44, tzinfo=UTC), step=1, purpose="deliberate")
CONFIG = GenerationConfig(max_output_tokens=2048, timeout_seconds=30)
TOOLS = [ToolSpec(name="get_service_health", description="health", input_schema={"type": "object", "properties": {"node": {"type": "string"}}, "required": ["node"]})]
CONVERSATION = [
    UserMessage(text="seed"),
    AssistantMessage(text="checking", tool_requests=[ToolRequest(id="call_1", name="get_service_health", arguments={"node": "checkout"})],
                     opaque=[{"type": "reasoning", "id": "rs_1", "encrypted_content": "enc", "summary": []}]),
    ToolResultsMessage(results=[ToolResult(request_id="call_1", content="evidence_id: EVD-000003")]),
    UserMessage(text="status"),
]


def _provider(responses: list) -> tuple[OpenAIProvider, FakeOpenAIClient]:
    client = FakeOpenAIClient(FixedResponder(responses))
    return OpenAIProvider(SETTINGS, client=client), client


def test_input_conversion_produces_responses_items():
    items = OpenAIProvider.to_input(CONVERSATION)
    assert items[0] == {"role": "user", "content": "seed"}
    assert items[1] == {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc", "summary": []}  # echoed before the call
    assert items[2] == {"role": "assistant", "content": "checking"}
    assert items[3] == {"type": "function_call", "call_id": "call_1", "name": "get_service_health", "arguments": json.dumps({"node": "checkout"})}
    assert items[4] == {"type": "function_call_output", "call_id": "call_1", "output": "evidence_id: EVD-000003"}
    assert items[5] == {"role": "user", "content": "status"}


def test_tool_schema_conversion():
    assert OpenAIProvider.to_tools(TOOLS) == [{"type": "function", "name": "get_service_health", "description": "health",
                                               "parameters": TOOLS[0].input_schema, "strict": False}]


async def test_complete_parses_message_function_calls_reasoning_and_usage():
    provider, client = _provider([oa_response(
        [oa_reasoning("rs_9", "cipher"), oa_message([oa_text("Looking.")]),
         oa_function_call("call_a", "get_service_health", {"node": "checkout"}),
         oa_function_call("call_b", "query_logs", {"node": "checkout", "limit": 5})],
        usage=oa_usage(500, 80, cached=200, reasoning=30))])
    turn = await provider.complete(SystemPrompt(text="sys"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert turn.text == "Looking." and turn.stop_reason == "tool_use"
    assert [(r.id, r.name, r.arguments) for r in turn.tool_requests] == [
        ("call_a", "get_service_health", {"node": "checkout"}), ("call_b", "query_logs", {"node": "checkout", "limit": 5})]
    assert turn.opaque == [{"type": "reasoning", "id": "rs_9", "encrypted_content": "cipher", "summary": []}]  # no summary text kept
    assert turn.usage.reported and turn.usage.input_tokens == 500 and turn.usage.output_tokens == 80
    assert turn.usage.cached_input_tokens == 200 and turn.usage.reasoning_output_tokens == 30
    kind, request = client.requests[0]
    assert kind == "create" and request["model"] == "test-openai-model" and request["instructions"] == "sys"
    assert request["store"] is False and request["include"] == ["reasoning.encrypted_content"]
    assert request["tool_choice"] == "auto" and request["parallel_tool_calls"] is True
    assert request["tools"][0]["type"] == "function" and request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] >= 2048 and request["timeout"] == 30
    assert type(turn).__module__.startswith("signalforge.") and type(turn.tool_requests[0]).__module__.startswith("signalforge.")


async def test_refusal_incomplete_and_malformed_arguments():
    provider, _ = _provider([oa_response([oa_message([oa_refusal("I cannot help with that.")])])])
    with pytest.raises(ProviderFailure) as info:
        await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert info.value.category == "refusal" and "cannot help" in str(info.value)

    provider, _ = _provider([oa_response([oa_message([oa_text("partial")])], status="incomplete", incomplete_reason="max_output_tokens")])
    turn = await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert turn.stop_reason == "max_output_tokens" and turn.text == "partial"

    provider, _ = _provider([oa_response([oa_message([oa_text("x")])], status="incomplete", incomplete_reason="content_filter")])
    with pytest.raises(ProviderFailure) as info:
        await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert info.value.category == "refusal"

    provider, _ = _provider([oa_response([oa_function_call("call_x", "query_logs", "{not json")])])
    with pytest.raises(ProviderFailure) as info:
        await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
    assert info.value.category == "malformed_output"


async def test_structured_output_via_output_parsed_and_fallback():
    draft = ReportDraft.model_validate(valid_draft())
    provider, client = _provider([oa_response([oa_message([oa_text(draft.model_dump_json())])], output_parsed=draft,
                                              output_text=draft.model_dump_json(), usage=oa_usage(50, 60))])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value == draft and result.usage.output_tokens == 60
    kind, request = client.requests[0]
    assert kind == "parse" and request["text_format"] is ReportDraft and "tools" not in request and request["store"] is False
    provider, _ = _provider([oa_response([oa_message([oa_text(draft.model_dump_json())])], output_text=draft.model_dump_json())])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value == draft
    provider, _ = _provider([oa_response([oa_message([oa_text("{}")])], output_text="{}")])
    result = await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert result.value is None and "did not validate" in (result.parse_error or "")
    provider, _ = _provider([oa_response([oa_message([oa_refusal("no")])])])
    with pytest.raises(ProviderFailure) as info:
        await provider.generate_structured(SystemPrompt(text="s"), CONVERSATION, schema=ReportDraft, context=CONTEXT, config=CONFIG)
    assert info.value.category == "refusal"


async def test_vendor_exceptions_are_normalised():
    class RateLimitError(Exception):
        status_code = 429

    class PermissionDeniedError(Exception):
        status_code = 403

    class APITimeoutError(Exception):
        pass

    class LengthFinishReasonError(Exception):
        pass

    for exc, category, retryable in [(RateLimitError("429"), "rate_limited", True), (PermissionDeniedError("403"), "permission", False),
                                     (APITimeoutError("t"), "timeout", True), (LengthFinishReasonError("len"), "context_exhausted", False)]:
        provider, _ = _provider([exc])
        with pytest.raises(ProviderFailure) as info:
            await provider.complete(SystemPrompt(text="s"), CONVERSATION, tools=TOOLS, context=CONTEXT, config=CONFIG)
        assert (info.value.category, info.value.retryable) == (category, retryable)


def test_real_sdk_exception_classes_classify_correctly():
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    assert classify_exception(openai.RateLimitError("rate", response=httpx2.Response(429, request=request), body=None), "openai").category == "rate_limited"
    assert classify_exception(openai.NotFoundError("The model `x` does not exist", response=httpx2.Response(404, request=request), body=None), "openai").category == "invalid_model"
    assert classify_exception(openai.APIConnectionError(request=request), "openai").category == "network"
    assert classify_exception(openai.InternalServerError("ise", response=httpx2.Response(500, request=request), body=None), "openai").category == "server_error"


def test_setup_failures_are_clear(monkeypatch):
    with pytest.raises(ProviderFailure) as info:
        OpenAIProvider(OpenAISettings(model=""), env={})
    assert info.value.category == "missing_model" and "SIGNALFORGE_OPENAI_MODEL" in str(info.value)
    with pytest.raises(ProviderFailure) as info:
        OpenAIProvider(SETTINGS, env={})
    assert info.value.category == "missing_api_key" and "ChatGPT subscription" in str(info.value)
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ProviderFailure) as info:
        OpenAIProvider(SETTINGS, env={"OPENAI_API_KEY": "sk-test-not-real-000000000000"})
    assert info.value.category == "missing_sdk" and "signalforge[openai]" in str(info.value)
