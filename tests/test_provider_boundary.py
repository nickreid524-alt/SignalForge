"""The neutral provider boundary: messages, fingerprints, provider labelling, factory behaviour."""

from __future__ import annotations

import inspect

import pytest
from pydantic import TypeAdapter

from signalforge.providers import base
from signalforge.providers.base import (
    AssistantMessage,
    Message,
    ProviderError,
    SystemPrompt,
    ToolRequest,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
    request_fingerprint,
)
from signalforge.providers.factory import PROVIDER_CHOICES, create_provider
from signalforge.providers.scripted import SCRIPTED_INFO, ScriptedDemoProvider


def test_messages_round_trip_through_the_discriminated_union():
    adapter = TypeAdapter(list[Message])
    conversation = [
        UserMessage(text="incident"),
        AssistantMessage(text="looking", tool_requests=[ToolRequest(id="a", name="get_alerts", arguments={"x": 1})],
                         opaque=[{"type": "thinking", "signature": "opaque-bytes"}]),
        ToolResultsMessage(results=[ToolResult(request_id="a", content="evidence_id: EVD-000001", is_error=False)]),
    ]
    dumped = adapter.dump_python(conversation, mode="json")
    assert [m["role"] for m in dumped] == ["user", "assistant", "tool_results"]
    assert adapter.validate_python(dumped) == conversation


def test_fingerprint_is_stable_and_sensitive():
    system = SystemPrompt(text="s")
    tools = [ToolSpec(name="t", description="d", input_schema={"type": "object"})]
    a = request_fingerprint(system, [UserMessage(text="x")], tools, None)
    b = request_fingerprint(system, [UserMessage(text="x")], tools, None)
    c = request_fingerprint(system, [UserMessage(text="y")], tools, None)
    d = request_fingerprint(system, [UserMessage(text="x")], tools, "ReportDraft")
    assert a == b and a != c and a != d and a.startswith("sha256:")


def test_scripted_provider_is_explicitly_labelled_non_llm():
    provider = ScriptedDemoProvider()
    assert provider.info is SCRIPTED_INFO
    assert provider.info.mode == "scripted" and provider.info.uses_llm is False
    assert "no language model" in provider.info.description.lower()
    assert "not an llm" in (inspect.getmodule(ScriptedDemoProvider).__doc__ or "").lower()


def test_factory_choices_and_failures():
    assert set(PROVIDER_CHOICES) == {"scripted", "replay", "anthropic", "openai"}
    assert isinstance(create_provider("scripted"), ScriptedDemoProvider)
    with pytest.raises(ProviderError, match="not available"):
        create_provider("anthropic")
    with pytest.raises(ProviderError, match="not available"):
        create_provider("openai")
    with pytest.raises(ProviderError, match="cassette"):
        create_provider("replay")
    with pytest.raises(ProviderError, match="unknown provider"):
        create_provider("gpt-oracle")


def test_boundary_module_has_no_vendor_sdk_imports():
    source = inspect.getsource(base)
    for forbidden in ("import anthropic", "import openai", "from anthropic", "from openai", "import mcp"):
        assert forbidden not in source, forbidden
    for name in ("ToolSpec", "ToolRequest", "ToolResult", "AssistantMessage", "ModelTurn", "StructuredResult"):
        cls = getattr(base, name)
        for annotation in cls.model_fields.values():
            assert "anthropic" not in str(annotation.annotation).lower()
            assert "openai" not in str(annotation.annotation).lower()
