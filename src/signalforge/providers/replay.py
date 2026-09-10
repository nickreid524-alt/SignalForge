"""Record and replay provider interactions deterministically.

``RecordingProvider`` wraps any provider and captures every turn keyed by the
request fingerprint. ``ReplayProvider`` serves those turns back without any
model, so adapter behaviour and whole investigations can be tested offline and
debugged step by step. A mismatch between the replayed conversation and the
recorded one is an error, not a silent fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from signalforge.providers.base import (
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelProvider,
    ModelTurn,
    ProviderError,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolSpec,
    request_fingerprint,
)

CASSETTE_VERSION = 1


class ReplayMismatch(ProviderError):
    pass


class CassetteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    purpose: str
    fingerprint: str
    kind: Literal["turn", "structured"]
    turn: ModelTurn | None = None
    schema_name: str | None = None
    structured_raw: dict[str, Any] | None = None
    parse_error: str | None = None


class Cassette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = CASSETTE_VERSION
    provider: ProviderInfo
    entries: list[CassetteEntry] = []

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        cassette = cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        if cassette.version != CASSETTE_VERSION:
            raise ProviderError(f"cassette version {cassette.version} not supported")
        return cassette


class RecordingProvider:
    """Pass-through provider that records what the inner provider returned."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner
        self.info = inner.info
        self.cassette = Cassette(provider=inner.info)

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        fingerprint = request_fingerprint(system, conversation, tools, None)
        turn = await self.inner.complete(system, conversation, tools=tools, context=context, config=config)
        self.cassette.entries.append(CassetteEntry(index=len(self.cassette.entries), purpose=context.purpose,
                                                   fingerprint=fingerprint, kind="turn", turn=turn))
        return turn

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        fingerprint = request_fingerprint(system, conversation, [], schema.__name__)
        result = await self.inner.generate_structured(system, conversation, schema=schema, context=context, config=config)
        self.cassette.entries.append(CassetteEntry(
            index=len(self.cassette.entries), purpose=context.purpose, fingerprint=fingerprint, kind="structured",
            schema_name=schema.__name__, structured_raw=result.raw, parse_error=result.parse_error))
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
            return StructuredResult(schema_name=schema.__name__, parse_error=entry.parse_error or "recorded call had no output")
        try:
            value = schema.model_validate(entry.structured_raw)
        except ValidationError as exc:
            return StructuredResult(schema_name=schema.__name__, raw=entry.structured_raw, parse_error=str(exc))
        return StructuredResult(schema_name=schema.__name__, value=value, raw=entry.structured_raw)
