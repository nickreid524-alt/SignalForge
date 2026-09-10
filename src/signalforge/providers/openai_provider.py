"""OpenAI Responses API adapter behind the neutral ModelProvider boundary.

Verified against the official ``openai`` Python SDK 3.12.0: ``responses.create``
with ``{"type": "function"}`` tools, ``function_call`` output items answered by
``function_call_output`` input items, and ``responses.parse(text_format=Model)``
for structured output (``output_parsed``). Requests use ``store=False`` and carry
encrypted reasoning items back verbatim; nothing hidden is persisted. Credentials
come from ``OPENAI_API_KEY``; a ChatGPT subscription is not an API credential.
"""

from __future__ import annotations

import json
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

PROVIDER_NAME = "openai"
SDK_PACKAGE = "openai"
MODEL_ENV = "SIGNALFORGE_OPENAI_MODEL"
KEY_ENVS: tuple[str, ...] = ("OPENAI_API_KEY",)
EFFORT_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class OpenAISettings:
    model: str
    max_output_tokens: int = 8192
    timeout_seconds: float = 120.0
    reasoning_effort: str | None = None
    store: bool = False
    max_retries: int = 1


def sdk_version() -> str | None:
    try:
        return metadata.version(SDK_PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def credentials_present(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return any(env.get(name) for name in KEY_ENVS)


class OpenAIProvider:
    def __init__(self, settings: OpenAISettings, *, client: Any | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        if not settings.model:
            raise ProviderFailure("missing_model", f"set {MODEL_ENV} to the OpenAI model id to use", provider=PROVIDER_NAME)
        if settings.reasoning_effort is not None and settings.reasoning_effort not in EFFORT_LEVELS:
            raise ProviderFailure("invalid_request", f"reasoning effort must be one of {', '.join(EFFORT_LEVELS)}",
                                  provider=PROVIDER_NAME)
        self.settings = settings
        if client is None:
            if not credentials_present(env):
                raise ProviderFailure("missing_api_key",
                                      f"set {KEY_ENVS[0]} (an API key from the OpenAI platform; a ChatGPT subscription "
                                      f"is not an API credential)", provider=PROVIDER_NAME)
            try:
                import openai
            except ImportError as exc:
                raise ProviderFailure("missing_sdk", 'the openai SDK is not installed; pip install "signalforge[openai]"',
                                      provider=PROVIDER_NAME) from exc
            client = openai.AsyncOpenAI(max_retries=settings.max_retries, timeout=settings.timeout_seconds)
        self._client = client
        self.info = ProviderInfo(
            name=PROVIDER_NAME, model=settings.model, mode="live", uses_llm=True,
            description=f"OpenAI Responses API, model {settings.model}. Calls a paid API; key read from the environment.",
            capabilities=ProviderCapabilities(supports_tools=True, supports_structured_output=True, supports_usage=True,
                                              supports_streaming=False),
        )

    # ------------------------------------------------------------------ neutral -> vendor
    @staticmethod
    def to_input(conversation: Conversation) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in conversation:
            if isinstance(message, UserMessage):
                items.append({"role": "user", "content": message.text})
            elif isinstance(message, AssistantMessage):
                items.extend(dict(b) for b in message.opaque)  # reasoning items echoed back verbatim
                if message.text:
                    items.append({"role": "assistant", "content": message.text})
                items.extend({"type": "function_call", "call_id": r.id, "name": r.name,
                              "arguments": json.dumps(r.arguments, sort_keys=True)} for r in message.tool_requests)
            elif isinstance(message, ToolResultsMessage):
                items.extend({"type": "function_call_output", "call_id": r.request_id, "output": r.content}
                             for r in message.results)
        return items

    @staticmethod
    def to_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [{"type": "function", "name": t.name, "description": t.description, "parameters": t.input_schema,
                 "strict": False} for t in tools]

    def _request(self, system: SystemPrompt, conversation: Conversation, config: GenerationConfig) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.settings.model,
            "instructions": system.text,
            "input": self.to_input(conversation),
            "max_output_tokens": max(self.settings.max_output_tokens, config.max_output_tokens),
            "store": self.settings.store,
            "timeout": min(self.settings.timeout_seconds, config.timeout_seconds),
        }
        if not self.settings.store:
            request["include"] = ["reasoning.encrypted_content"]
        effort = config.effort or self.settings.reasoning_effort
        if effort:
            request["reasoning"] = {"effort": effort}
        return request

    # ------------------------------------------------------------------ vendor -> neutral
    @staticmethod
    def usage_from(usage: Any) -> ModelUsage:
        if usage is None:
            return ModelUsage(reported=False)
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return ModelUsage(
            reported=True,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_input_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            reasoning_output_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        )

    @staticmethod
    def _stop_reason(response: Any) -> str:
        status = getattr(response, "status", None)
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            if reason == "content_filter":
                raise ProviderFailure("refusal", "the response was cut off by the content filter", provider=PROVIDER_NAME)
            return "max_output_tokens" if reason == "max_output_tokens" else f"incomplete:{reason}"
        return "end_turn"

    def parse_response(self, response: Any, latency_ms: float) -> ModelTurn:
        stop = self._stop_reason(response)
        texts: list[str] = []
        requests: list[ToolRequest] = []
        opaque: list[dict[str, Any]] = []
        for item in getattr(response, "output", []) or []:
            kind = getattr(item, "type", None)
            if kind == "message":
                for part in getattr(item, "content", []) or []:
                    part_kind = getattr(part, "type", None)
                    if part_kind == "output_text":
                        texts.append(part.text)
                    elif part_kind == "refusal":
                        raise ProviderFailure("refusal", f"the model refused: {getattr(part, 'refusal', '')}", provider=PROVIDER_NAME)
            elif kind == "function_call":
                raw = getattr(item, "arguments", "") or "{}"
                try:
                    arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except json.JSONDecodeError as exc:
                    raise ProviderFailure("malformed_output", f"function_call arguments are not valid JSON: {exc}",
                                          provider=PROVIDER_NAME) from exc
                if not isinstance(arguments, dict):
                    raise ProviderFailure("malformed_output", "function_call arguments must be a JSON object", provider=PROVIDER_NAME)
                requests.append(ToolRequest(id=str(item.call_id), name=str(item.name), arguments=arguments))
            elif kind == "reasoning":
                # Only the opaque handle is kept (id + encrypted payload); summaries/text are not requested or stored.
                opaque.append({"type": "reasoning", "id": getattr(item, "id", None),
                               "encrypted_content": getattr(item, "encrypted_content", None), "summary": []})
        if requests:
            stop = "tool_use" if stop == "end_turn" else stop
        return ModelTurn(text="\n".join(texts) or None, tool_requests=requests, opaque=opaque,
                         usage=self.usage_from(getattr(response, "usage", None)), latency_ms=latency_ms, stop_reason=stop)

    # ------------------------------------------------------------------ provider protocol
    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        request = self._request(system, conversation, config)
        if tools:
            request["tools"] = self.to_tools(tools)
            request["tool_choice"] = "auto"
            request["parallel_tool_calls"] = True
        started = perf_counter()
        try:
            response = await self._client.responses.create(**request)
        except ProviderFailure:
            raise
        except Exception as exc:
            raise classify_exception(exc, PROVIDER_NAME) from exc
        return self.parse_response(response, round((perf_counter() - started) * 1000, 3))

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        request = self._request(system, conversation, config)
        started = perf_counter()
        try:
            response = await self._client.responses.parse(**request, text_format=schema)
        except ProviderFailure:
            raise
        except Exception as exc:
            raise classify_exception(exc, PROVIDER_NAME) from exc
        latency = round((perf_counter() - started) * 1000, 3)
        self._stop_reason(response)
        usage = self.usage_from(getattr(response, "usage", None))
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", None) == "refusal":
                        raise ProviderFailure("refusal", f"the model refused: {getattr(part, 'refusal', '')}", provider=PROVIDER_NAME)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            text = getattr(response, "output_text", "") or ""
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
