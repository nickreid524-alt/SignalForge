"""Fake vendor SDK clients and vendor-shaped response builders. No network, no SDK required.

Also provides *inverse* mappers (vendor request -> neutral conversation) so the
ScriptedDemoProvider can act as the "brain" behind a fake Anthropic or OpenAI
server. That lets the real adapters be exercised end to end through the
orchestrator without any model.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from typing import Any

from signalforge.providers.base import (
    AssistantMessage,
    Conversation,
    GenerationConfig,
    InvestigationContext,
    SystemPrompt,
    ToolRequest,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from signalforge.providers.scripted import ScriptedDemoProvider
from signalforge.reports.schema import ReportDraft

# ------------------------------------------------------------------ Anthropic-shaped objects


def ant_text(text: str) -> NS:
    return NS(type="text", text=text)


def ant_tool_use(id: str, name: str, input: dict) -> NS:
    return NS(type="tool_use", id=id, name=name, input=input)


def ant_thinking(signature: str = "sig-opaque", thinking: str = "") -> NS:
    return NS(type="thinking", signature=signature, thinking=thinking)


def ant_usage(inp: int = 120, out: int = 30, cache_read: int = 0, cache_create: int = 0) -> NS:
    return NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_create)


def ant_message(content: list, stop_reason: str = "end_turn", usage: NS | None = None, stop_details: NS | None = None,
                parsed_output: Any = None) -> NS:
    return NS(content=content, stop_reason=stop_reason, usage=usage or ant_usage(), stop_details=stop_details,
              parsed_output=parsed_output, model="fake-anthropic-model")


class _AntMessages:
    def __init__(self, responder, requests: list) -> None:
        self._responder = responder
        self._requests = requests

    async def create(self, **kwargs):
        self._requests.append(("create", kwargs))
        return await self._responder.create(kwargs)

    async def parse(self, **kwargs):
        self._requests.append(("parse", kwargs))
        return await self._responder.parse(kwargs)


class FakeAnthropicClient:
    def __init__(self, responder) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.messages = _AntMessages(responder, self.requests)


# ------------------------------------------------------------------ OpenAI-shaped objects


def oa_text(text: str) -> NS:
    return NS(type="output_text", text=text, annotations=[])


def oa_refusal(text: str) -> NS:
    return NS(type="refusal", refusal=text)


def oa_message(parts: list) -> NS:
    return NS(type="message", role="assistant", content=parts, id="msg_1", status="completed")


def oa_function_call(call_id: str, name: str, arguments: dict | str) -> NS:
    return NS(type="function_call", call_id=call_id, name=name, id=f"fc_{call_id}",
              arguments=arguments if isinstance(arguments, str) else json.dumps(arguments))


def oa_reasoning(id: str = "rs_1", encrypted: str = "enc-opaque") -> NS:
    return NS(type="reasoning", id=id, encrypted_content=encrypted, summary=[NS(type="summary_text", text="should not persist")])


def oa_usage(inp: int = 200, out: int = 40, cached: int = 50, reasoning: int = 10) -> NS:
    return NS(input_tokens=inp, output_tokens=out, total_tokens=inp + out,
              input_tokens_details=NS(cached_tokens=cached), output_tokens_details=NS(reasoning_tokens=reasoning))


def oa_response(output: list, status: str = "completed", usage: NS | None = None, incomplete_reason: str | None = None,
                output_parsed: Any = None, output_text: str = "") -> NS:
    return NS(output=output, status=status, usage=usage or oa_usage(),
              incomplete_details=NS(reason=incomplete_reason) if incomplete_reason else None,
              output_parsed=output_parsed, output_text=output_text, model="fake-openai-model")


class _OaResponses:
    def __init__(self, responder, requests: list) -> None:
        self._responder = responder
        self._requests = requests

    async def create(self, **kwargs):
        self._requests.append(("create", kwargs))
        return await self._responder.create(kwargs)

    async def parse(self, **kwargs):
        self._requests.append(("parse", kwargs))
        return await self._responder.parse(kwargs)


class FakeOpenAIClient:
    def __init__(self, responder) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.responses = _OaResponses(responder, self.requests)


# ------------------------------------------------------------------ responders


class FixedResponder:
    """Returns prebuilt vendor responses in order (create and parse share one queue)."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)

    async def _next(self) -> Any:
        if not self.responses:
            raise AssertionError("FixedResponder exhausted")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def create(self, kwargs: dict) -> Any:
        return await self._next()

    async def parse(self, kwargs: dict) -> Any:
        return await self._next()


# ------------------------------------------------------------------ inverse mappers (vendor request -> neutral)


def anthropic_request_to_neutral(kwargs: dict) -> tuple[SystemPrompt, Conversation]:
    conversation: Conversation = []
    for message in kwargs["messages"]:
        blocks = message["content"] if isinstance(message["content"], list) else [{"type": "text", "text": message["content"]}]
        if message["role"] == "user":
            texts = [b["text"] for b in blocks if b.get("type") == "text"]
            results = [ToolResult(request_id=b["tool_use_id"], content=b["content"], is_error=bool(b.get("is_error")))
                       for b in blocks if b.get("type") == "tool_result"]
            if results:
                conversation.append(ToolResultsMessage(results=results))
            if texts:
                conversation.append(UserMessage(text="\n".join(texts)))
        else:
            text = "\n".join(b["text"] for b in blocks if b.get("type") == "text") or None
            requests = [ToolRequest(id=b["id"], name=b["name"], arguments=dict(b["input"])) for b in blocks if b.get("type") == "tool_use"]
            opaque = [b for b in blocks if b.get("type") in ("thinking", "redacted_thinking")]
            conversation.append(AssistantMessage(text=text, tool_requests=requests, opaque=opaque))
    return SystemPrompt(text=kwargs.get("system", "")), conversation


def openai_request_to_neutral(kwargs: dict) -> tuple[SystemPrompt, Conversation]:
    conversation: Conversation = []
    pending_requests: list[ToolRequest] = []
    pending_text: str | None = None
    pending_results: list[ToolResult] = []

    def flush_assistant() -> None:
        nonlocal pending_requests, pending_text
        if pending_requests or pending_text is not None:
            conversation.append(AssistantMessage(text=pending_text, tool_requests=pending_requests))
        pending_requests, pending_text = [], None

    def flush_results() -> None:
        nonlocal pending_results
        if pending_results:
            conversation.append(ToolResultsMessage(results=pending_results))
        pending_results = []

    for item in kwargs["input"]:
        kind = item.get("type")
        if kind == "function_call":
            flush_results()
            pending_requests.append(ToolRequest(id=item["call_id"], name=item["name"], arguments=json.loads(item["arguments"])))
        elif kind == "function_call_output":
            flush_assistant()
            pending_results.append(ToolResult(request_id=item["call_id"], content=item["output"]))
        elif kind == "reasoning":
            continue
        elif item.get("role") == "assistant":
            flush_results()
            pending_text = item["content"]
        elif item.get("role") == "user":
            flush_assistant()
            flush_results()
            conversation.append(UserMessage(text=item["content"]))
    flush_assistant()
    flush_results()
    return SystemPrompt(text=kwargs.get("instructions", "")), conversation


_SEED = re.compile(r"incident_id: (?P<incident>\S+).*?affected_service: (?P<service>\S+).*?investigation_clock: (?P<clock>\S+)", re.S)


def context_from_conversation(conversation: Conversation, purpose: str) -> InvestigationContext:
    seed = next((m.text for m in conversation if isinstance(m, UserMessage) and "INCIDENT UNDER INVESTIGATION" in m.text), "")
    match = _SEED.search(seed)
    assert match, "seed message not found in conversation"
    clock = datetime.fromisoformat(match["clock"].replace("Z", "+00:00"))
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    steps = sum(1 for m in conversation if isinstance(m, AssistantMessage))
    return InvestigationContext(investigation_id=f"fake-{match['incident'].lower()}", incident_id=match["incident"],
                                affected_service=match["service"], investigation_clock=clock, step=steps, purpose=purpose)  # type: ignore[arg-type]


class ScriptedAnthropicResponder:
    """A fake Anthropic server whose 'model' is the scripted playbook."""

    def __init__(self) -> None:
        self.brain = ScriptedDemoProvider()

    async def create(self, kwargs: dict) -> NS:
        system, conversation = anthropic_request_to_neutral(kwargs)
        context = context_from_conversation(conversation, "deliberate")
        tools = [ToolSpec(name=t["name"], description=t.get("description", ""), input_schema=t["input_schema"]) for t in kwargs.get("tools", [])]
        turn = await self.brain.complete(system, conversation, tools=tools, context=context, config=GenerationConfig())
        content: list = [ant_thinking()]
        if turn.text:
            content.append(ant_text(turn.text))
        content.extend(ant_tool_use(r.id, r.name, r.arguments) for r in turn.tool_requests)
        return ant_message(content, stop_reason="tool_use" if turn.tool_requests else "end_turn", usage=ant_usage(150, 40, cache_read=20))

    async def parse(self, kwargs: dict) -> NS:
        system, conversation = anthropic_request_to_neutral(kwargs)
        context = context_from_conversation(conversation, "report")
        result = await self.brain.generate_structured(system, conversation, schema=kwargs["output_format"], context=context,
                                                      config=GenerationConfig())
        draft = result.value
        assert isinstance(draft, ReportDraft)
        return ant_message([ant_text(draft.model_dump_json())], usage=ant_usage(400, 300), parsed_output=draft)


class ScriptedOpenAIResponder:
    """A fake OpenAI Responses server whose 'model' is the scripted playbook."""

    def __init__(self) -> None:
        self.brain = ScriptedDemoProvider()

    async def create(self, kwargs: dict) -> NS:
        system, conversation = openai_request_to_neutral(kwargs)
        context = context_from_conversation(conversation, "deliberate")
        tools = [ToolSpec(name=t["name"], description=t.get("description", ""), input_schema=t["parameters"]) for t in kwargs.get("tools", [])]
        turn = await self.brain.complete(system, conversation, tools=tools, context=context, config=GenerationConfig())
        output: list = [oa_reasoning()]
        if turn.text:
            output.append(oa_message([oa_text(turn.text)]))
        output.extend(oa_function_call(r.id, r.name, r.arguments) for r in turn.tool_requests)
        return oa_response(output, usage=oa_usage(180, 45, cached=60, reasoning=12))

    async def parse(self, kwargs: dict) -> NS:
        system, conversation = openai_request_to_neutral(kwargs)
        context = context_from_conversation(conversation, "report")
        result = await self.brain.generate_structured(system, conversation, schema=kwargs["text_format"], context=context,
                                                      config=GenerationConfig())
        draft = result.value
        assert isinstance(draft, ReportDraft)
        text = draft.model_dump_json()
        return oa_response([oa_message([oa_text(text)])], usage=oa_usage(500, 320), output_parsed=draft, output_text=text)
