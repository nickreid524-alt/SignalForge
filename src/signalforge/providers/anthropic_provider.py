"""Anthropic Messages API adapter behind the neutral ModelProvider boundary.

Verified against the official ``anthropic`` Python SDK 1.5.0 (Messages API):
``messages.create`` with native ``tools`` / ``tool_use`` blocks, and
``messages.parse(output_format=PydanticModel)`` for structured output
(``ParsedMessage.parsed_output``). The adapter only translates; it never executes
tools. Credentials come from the environment (``ANTHROPIC_API_KEY``); a consumer
Claude subscription is not an API credential.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from signalforge.providers.base import (
    AssistantMessage,
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelTurn,
    ModelUsage,
    ProviderCapabilities,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolRequest,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from signalforge.providers.errors import ProviderFailure, classify_exception

PROVIDER_NAME = "anthropic"
SDK_PACKAGE = "anthropic"
MODEL_ENV = "SIGNALFORGE_ANTHROPIC_MODEL"
KEY_ENVS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class AnthropicSettings:
    model: str
    max_output_tokens: int = 8192
    timeout_seconds: float = 120.0
    effort: str | None = None
    max_retries: int = 1


def sdk_version() -> str | None:
    try:
        return metadata.version(SDK_PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def credentials_present(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return any(env.get(name) for name in KEY_ENVS)


def _dump(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    return {k: v for k, v in vars(block).items() if not k.startswith("_")}


class AnthropicProvider:
    def __init__(self, settings: AnthropicSettings, *, client: Any | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        if not settings.model:
            raise ProviderFailure("missing_model", f"set {MODEL_ENV} to the Claude model id to use", provider=PROVIDER_NAME)
        if settings.effort is not None and settings.effort not in EFFORT_LEVELS:
            raise ProviderFailure("invalid_request", f"effort must be one of {', '.join(EFFORT_LEVELS)}", provider=PROVIDER_NAME)
        self.settings = settings
        if client is None:
            if not credentials_present(env):
                raise ProviderFailure("missing_api_key",
                                      f"set {KEY_ENVS[0]} (an API key from the Anthropic Console; a Claude consumer "
                                      f"subscription is not an API credential)", provider=PROVIDER_NAME)
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderFailure("missing_sdk", 'the anthropic SDK is not installed; pip install "signalforge[anthropic]"',
                                      provider=PROVIDER_NAME) from exc
            client = anthropic.AsyncAnthropic(max_retries=settings.max_retries, timeout=settings.timeout_seconds)
        self._client = client
        self.info = ProviderInfo(
            name=PROVIDER_NAME, model=settings.model, mode="live", uses_llm=True,
            description=f"Anthropic Messages API, model {settings.model}. Calls a paid API; key read from the environment.",
            capabilities=ProviderCapabilities(supports_tools=True, supports_structured_output=True, supports_usage=True,
                                              supports_streaming=False),
        )

    # ------------------------------------------------------------------ neutral -> vendor
    @staticmethod
    def to_messages(conversation: Conversation) -> list[dict[str, Any]]:
        """Neutral conversation to Messages API turns. Consecutive same-role messages are merged
        (the API requires user/assistant alternation; tool results and follow-up text share one user turn)."""
        messages: list[dict[str, Any]] = []

        def push(role: str, blocks: list[dict[str, Any]]) -> None:
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})

        for message in conversation:
            if isinstance(message, UserMessage):
                push("user", [{"type": "text", "text": message.text}])
            elif isinstance(message, AssistantMessage):
                blocks: list[dict[str, Any]] = [dict(b) for b in message.opaque]
                if message.text:
                    blocks.append({"type": "text", "text": message.text})
                blocks.extend({"type": "tool_use", "id": r.id, "name": r.name, "input": dict(r.arguments)}
                              for r in message.tool_requests)
                if not blocks:
                    blocks.append({"type": "text", "text": "(no content)"})
                push("assistant", blocks)
            elif isinstance(message, ToolResultsMessage):
                push("user", [{"type": "tool_result", "tool_use_id": r.request_id, "content": r.content,
                               "is_error": r.is_error} for r in message.results])
        return messages

    @staticmethod
    def to_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]

    def _request(self, system: SystemPrompt, conversation: Conversation, config: GenerationConfig) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": max(self.settings.max_output_tokens, config.max_output_tokens),
            "system": system.text,
            "messages": self.to_messages(conversation),
            "timeout": min(self.settings.timeout_seconds, config.timeout_seconds),
        }
        effort = config.effort or self.settings.effort
        if effort:
            request["output_config"] = {"effort": effort}
        return request

    # ------------------------------------------------------------------ vendor -> neutral
    @staticmethod
    def usage_from(usage: Any) -> ModelUsage:
        if usage is None:
            return ModelUsage(reported=False)
        return ModelUsage(
            reported=True,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )

    @staticmethod
    def _check_stop(response: Any) -> str:
        stop = getattr(response, "stop_reason", None) or "end_turn"
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise ProviderFailure("refusal", f"the model refused to continue (category={category})", provider=PROVIDER_NAME)
        if stop == "model_context_window_exceeded":
            raise ProviderFailure("context_exhausted", "the conversation exceeded the model context window", provider=PROVIDER_NAME)
        return stop

    def parse_message(self, response: Any, latency_ms: float) -> ModelTurn:
        stop = self._check_stop(response)
        texts: list[str] = []
        requests: list[ToolRequest] = []
        opaque: list[dict[str, Any]] = []
        for block in getattr(response, "content", []) or []:
            kind = getattr(block, "type", None)
            if kind == "text":
                texts.append(block.text)
            elif kind == "tool_use":
                arguments = block.input if isinstance(block.input, dict) else {}
                requests.append(ToolRequest(id=str(block.id), name=str(block.name), arguments=dict(arguments)))
            elif kind in ("thinking", "redacted_thinking"):
                opaque.append(_dump(block))  # echoed back to the same model; never persisted
        return ModelTurn(text="\n".join(texts) or None, tool_requests=requests, opaque=opaque,
                         usage=self.usage_from(getattr(response, "usage", None)), latency_ms=latency_ms, stop_reason=stop)

    # ------------------------------------------------------------------ provider protocol
    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        request = self._request(system, conversation, config)
        if tools:
            request["tools"] = self.to_tools(tools)
            request["tool_choice"] = {"type": "auto"}  # forced tool choice is not supported on every model
        started = perf_counter()
        try:
            response = await self._client.messages.create(**request)
        except ProviderFailure:
            raise
        except Exception as exc:
            raise classify_exception(exc, PROVIDER_NAME) from exc
        return self.parse_message(response, round((perf_counter() - started) * 1000, 3))

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        request = self._request(system, conversation, config)
        started = perf_counter()
        try:
            response = await self._client.messages.parse(**request, output_format=schema)
        except ProviderFailure:
            raise
        except Exception as exc:
            raise classify_exception(exc, PROVIDER_NAME) from exc
        latency = round((perf_counter() - started) * 1000, 3)
        self._check_stop(response)
        usage = self.usage_from(getattr(response, "usage", None))
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            text = "\n".join(b.text for b in getattr(response, "content", []) or [] if getattr(b, "type", None) == "text")
            try:
                parsed = schema.model_validate_json(text) if text.strip() else None
            except Exception as exc:
                return StructuredResult(schema_name=schema.__name__, parse_error=f"structured output did not validate: {exc}",
                                        usage=usage, latency_ms=latency)
        if parsed is None:
            return StructuredResult(schema_name=schema.__name__, parse_error="the model returned no structured output",
                                    usage=usage, latency_ms=latency)
        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate(parsed.model_dump() if hasattr(parsed, "model_dump") else parsed)
            except Exception as exc:
                return StructuredResult(schema_name=schema.__name__, parse_error=f"structured output did not validate: {exc}",
                                        usage=usage, latency_ms=latency)
        return StructuredResult(schema_name=schema.__name__, value=parsed, raw=parsed.model_dump(mode="json"),
                                usage=usage, latency_ms=latency)
