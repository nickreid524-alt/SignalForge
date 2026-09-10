"""Test doubles for the provider boundary. No network, no model."""

from __future__ import annotations

import anyio
from pydantic import BaseModel

from signalforge.providers.base import (
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelTurn,
    ProviderError,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolRequest,
    ToolSpec,
)

FAKE_INFO = ProviderInfo(name="sequence-test-double", model="none", mode="scripted", uses_llm=False,
                         description="Test double that replays a fixed sequence of turns and drafts.")


def turn(*requests: tuple[str, str, dict], text: str | None = None) -> ModelTurn:
    return ModelTurn(text=text, tool_requests=[ToolRequest(id=i, name=n, arguments=a) for i, n, a in requests],
                     stop_reason="tool_use" if requests else "end_turn")


class SequenceProvider:
    """Emits the given turns in order (then finishes), then the given drafts in order.

    ``drafts`` entries may be a BaseModel instance, a raw dict (validated against the requested schema),
    or None (simulates unparseable output). ``fail_first`` raises ProviderError on the first N calls;
    ``delay`` sleeps before answering (for timeout tests).
    """

    info = FAKE_INFO

    def __init__(self, turns: list[ModelTurn], drafts: list[object], *, fail_first: int = 0, delay: float = 0.0) -> None:
        self.turns = list(turns)
        self.drafts = list(drafts)
        self.fail_first = fail_first
        self.delay = delay
        self.calls: list[str] = []
        self.contexts: list[InvestigationContext] = []

    async def _gate(self, purpose: str, context: InvestigationContext) -> None:
        self.calls.append(purpose)
        self.contexts.append(context)
        if self.delay:
            await anyio.sleep(self.delay)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ProviderError("simulated provider failure")

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        await self._gate("deliberate", context)
        if self.turns:
            return self.turns.pop(0)
        return turn(("finish", "finish_investigation", {"reason": "sequence exhausted"}))

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        await self._gate(context.purpose, context)
        if not self.drafts:
            return StructuredResult(schema_name=schema.__name__, parse_error="no draft available")
        draft = self.drafts.pop(0)
        if draft is None:
            return StructuredResult(schema_name=schema.__name__, parse_error="simulated unparseable output")
        if isinstance(draft, BaseModel):
            return StructuredResult(schema_name=schema.__name__, value=draft, raw=draft.model_dump(mode="json"))
        try:
            value = schema.model_validate(draft)
        except Exception as exc:
            return StructuredResult(schema_name=schema.__name__, raw=dict(draft), parse_error=str(exc))
        return StructuredResult(schema_name=schema.__name__, value=value, raw=value.model_dump(mode="json"))


def valid_draft(**overrides) -> dict:
    """A draft that is valid once EVD-000002 (topology) and EVD-000003 (a successful tool call) exist."""
    base = {
        "summary": "Test summary long enough to satisfy the schema minimum length.",
        "status": "root_cause_identified",
        "confidence": 0.8,
        "primary_hypothesis": {
            "id": "H1", "statement": "The service health check shows degradation", "category": "deployment_regression",
            "status": "supported", "confidence": 0.8, "supporting_evidence_ids": ["EVD-000003", "EVD-000002"],
            "contradicting_evidence_ids": [], "reasoning": "test",
        },
        "hypotheses_considered": [{
            "id": "H1", "statement": "The service health check shows degradation", "category": "deployment_regression",
            "status": "supported", "confidence": 0.8, "supporting_evidence_ids": ["EVD-000003", "EVD-000002"],
            "contradicting_evidence_ids": [], "reasoning": "test",
        }],
        "key_findings": [{"statement": "Health is degraded", "kind": "OBSERVED", "evidence_ids": ["EVD-000003"]}],
        "contradicting_evidence": [],
        "recommended_actions": [{"action": "Investigate further", "rationale": "Because", "priority": "P2",
                                 "kind": "diagnostic", "evidence_ids": []}],
        "unknowns": [{"statement": "Whether this persists", "kind": "UNKNOWN", "evidence_ids": []}],
        "limitations": ["test double"],
    }
    base.update(overrides)
    return base
