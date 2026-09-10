"""Record and replay provider interactions deterministically (cassette format v2).

A cassette stores the minimum neutral contract needed to reproduce behaviour:
the provider identity, and for every call the neutral request (system prompt,
conversation, tool names, schema, context), the neutral response (turn or
structured output), usage and latency, plus a fingerprint of the request. It
never stores API keys, auth headers, raw HTTP traffic, vendor SDK objects or
provider-native opaque blocks (thinking / reasoning items). All text passes
through redaction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from signalforge.providers.base import (
    Conversation,
    GenerationConfig,
    InvestigationContext,
    Message,
    ModelProvider,
    ModelTurn,
    ModelUsage,
    ProviderError,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolSpec,
    dump_conversation,
    request_fingerprint,
    strip_opaque,
)
from signalforge.redaction import redact

CASSETTE_VERSION = 2
_CONVERSATION = TypeAdapter(list[Message])


class ReplayMismatch(ProviderError):
    pass


class CassetteRequest(BaseModel):
    """Neutral request representation: enough to inspect and to re-derive the fingerprint."""

    model_config = ConfigDict(extra="forbid")

    system: str
    conversation: list[Message]
    tool_names: list[str]
    tools_digest: str
    schema_name: str | None
    context: InvestigationContext


class CassetteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    purpose: str
    fingerprint: str
    kind: Literal["turn", "structured"]
    request: CassetteRequest
    turn: ModelTurn | None = None
    schema_name: str | None = None
    structured_raw: dict[str, Any] | None = None
    parse_error: str | None = None
    usage: ModelUsage = ModelUsage()
    latency_ms: float = 0.0


class Cassette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = CASSETTE_VERSION
    provider: ProviderInfo
    model: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sanitized: bool = True
    entries: list[CassetteEntry] = []

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("version") != CASSETTE_VERSION:
            raise ProviderError(f"cassette version {raw.get('version')} not supported (expected {CASSETTE_VERSION})")
        return cls.model_validate(raw)


def _sanitize_conversation(conversation: Conversation) -> list[Message]:
    text = json.dumps(dump_conversation(conversation), ensure_ascii=False, default=str)
    return _CONVERSATION.validate_json(redact(text) or "[]")


def _sanitize_turn(turn: ModelTurn) -> ModelTurn:
    stripped = strip_opaque(turn)
    return stripped.model_copy(update={"text": redact(stripped.text)})


def _tools_digest(tools: list[ToolSpec]) -> str:
    encoded = json.dumps([t.model_dump(mode="json") for t in tools], sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sanitize_raw(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    return json.loads(redact(json.dumps(raw, ensure_ascii=False, default=str)) or "{}")


class RecordingProvider:
    """Pass-through provider that records what the inner provider returned, sanitised."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner
        self.info = inner.info
        self.cassette = Cassette(provider=inner.info, model=inner.info.model)

    def _request(self, system: SystemPrompt, conversation: Conversation, tools: list[ToolSpec],
                 schema_name: str | None, context: InvestigationContext) -> CassetteRequest:
        return CassetteRequest(system=redact(system.text) or "", conversation=_sanitize_conversation(conversation),
                               tool_names=[t.name for t in tools], tools_digest=_tools_digest(tools),
                               schema_name=schema_name, context=context)

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        fingerprint = request_fingerprint(system, conversation, tools, None)
        turn = await self.inner.complete(system, conversation, tools=tools, context=context, config=config)
        self.cassette.entries.append(CassetteEntry(
            index=len(self.cassette.entries), purpose=context.purpose, fingerprint=fingerprint, kind="turn",
            request=self._request(system, conversation, tools, None, context), turn=_sanitize_turn(turn),
            usage=turn.usage, latency_ms=turn.latency_ms))
        return turn

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        fingerprint = request_fingerprint(system, conversation, [], schema.__name__)
        result = await self.inner.generate_structured(system, conversation, schema=schema, context=context, config=config)
        self.cassette.entries.append(CassetteEntry(
            index=len(self.cassette.entries), purpose=context.purpose, fingerprint=fingerprint, kind="structured",
            request=self._request(system, conversation, [], schema.__name__, context), schema_name=schema.__name__,
            structured_raw=_sanitize_raw(result.raw), parse_error=redact(result.parse_error), usage=result.usage,
            latency_ms=result.latency_ms))
        return result


class ReplayProvider:
    """Serves recorded turns in order. ``strict`` also requires matching request fingerprints."""

    def __init__(self, cassette: Cassette, *, strict: bool = True) -> None:
        self.cassette = cassette
        self.strict = strict
        self.cursor = 0
        self.info = ProviderInfo(
            name=f"replay:{cassette.provider.name}", model=cassette.provider.model, mode="replay", uses_llm=False,
            description=f"Deterministic replay of {len(cassette.entries)} recorded interaction(s) from "
                        f"{cassette.provider.name} ({cassette.provider.mode}). No model is called.",
            capabilities=cassette.provider.capabilities,
        )

    @classmethod
    def from_file(cls, path: str | Path, *, strict: bool = True) -> ReplayProvider:
        return cls(Cassette.load(path), strict=strict)

    def _next(self, kind: str, fingerprint: str) -> CassetteEntry:
        if self.cursor >= len(self.cassette.entries):
            raise ReplayMismatch(f"cassette exhausted after {self.cursor} entries; no recorded {kind} available")
        entry = self.cassette.entries[self.cursor]
        self.cursor += 1
        if entry.kind != kind:
            raise ReplayMismatch(f"entry {entry.index} is a {entry.kind}, but a {kind} was requested")
        if self.strict and entry.fingerprint != fingerprint:
            raise ReplayMismatch(f"entry {entry.index} fingerprint mismatch: the conversation diverged from the recording")
        return entry

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        entry = self._next("turn", request_fingerprint(system, conversation, tools, None))
        assert entry.turn is not None
        return entry.turn

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        entry = self._next("structured", request_fingerprint(system, conversation, [], schema.__name__))
        if entry.structured_raw is None:
            return StructuredResult(schema_name=schema.__name__, parse_error=entry.parse_error or "recorded call had no output",
                                    usage=entry.usage, latency_ms=entry.latency_ms)
        try:
            value = schema.model_validate(entry.structured_raw)
        except ValidationError as exc:
            return StructuredResult(schema_name=schema.__name__, raw=entry.structured_raw, parse_error=str(exc),
                                    usage=entry.usage, latency_ms=entry.latency_ms)
        return StructuredResult(schema_name=schema.__name__, value=value, raw=entry.structured_raw, usage=entry.usage,
                                latency_ms=entry.latency_ms)
