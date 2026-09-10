"""The model-provider boundary.

Everything that crosses this line is a SignalForge type. Providers translate
these neutral messages to and from a vendor SDK (later phases) or synthesise
them deterministically (scripted / replay). The orchestrator never sees a
vendor object, and no provider owns a tool loop: a provider turns one
conversation into one turn.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# Every rendered evidence result starts with this line so any provider can locate evidence ids.
EVIDENCE_HEADER_PREFIX = "evidence_id:"


class ProviderError(RuntimeError):
    """A provider could not produce a turn (transport, quota, malformed output...)."""


class NeutralModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSpec(NeutralModel):
    """What the model is told it can call: MCP tools and the orchestrator's local actions alike."""

    name: str
    description: str
    input_schema: dict[str, Any]
    local: bool = Field(default=False, description="True for orchestrator actions that never reach MCP.")


class ToolRequest(NeutralModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}


class ToolResult(NeutralModel):
    request_id: str
    content: str
    is_error: bool = False


class SystemPrompt(NeutralModel):
    text: str


class UserMessage(NeutralModel):
    role: Literal["user"] = "user"
    text: str


class AssistantMessage(NeutralModel):
    role: Literal["assistant"] = "assistant"
    text: str | None = None
    tool_requests: list[ToolRequest] = []
    opaque: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Provider-native blocks (e.g. thinking) replayed verbatim on the same model; never interpreted.",
    )


class ToolResultsMessage(NeutralModel):
    role: Literal["tool_results"] = "tool_results"
    results: list[ToolResult]


Message = Annotated[UserMessage | AssistantMessage | ToolResultsMessage, Field(discriminator="role")]
Conversation = list[Message]


class ModelUsage(NeutralModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelTurn(NeutralModel):
    text: str | None = None
    tool_requests: list[ToolRequest] = []
    opaque: list[dict[str, Any]] = []
    usage: ModelUsage = ModelUsage()
    latency_ms: float = 0.0
    stop_reason: str = "end_turn"


class StructuredResult(NeutralModel):
    """Result of a structured-output request. ``value`` is an instance of the requested schema or None."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    schema_name: str
    value: Any = None
    raw: dict[str, Any] | None = None
    parse_error: str | None = None
    usage: ModelUsage = ModelUsage()
    latency_ms: float = 0.0


class ProviderInfo(NeutralModel):
    name: str
    model: str
    mode: Literal["scripted", "replay", "live"]
    uses_llm: bool
    description: str


class InvestigationContext(NeutralModel):
    """Facts the orchestrator already established and shares with the provider. Never ground truth."""

    investigation_id: str
    incident_id: str
    affected_service: str
    investigation_clock: datetime
    step: int
    purpose: Literal["deliberate", "report", "repair"]
    budget_remaining: dict[str, int] = {}


class GenerationConfig(NeutralModel):
    max_output_tokens: int = 4096
    temperature: float | None = None
    effort: str | None = None
    timeout_seconds: float = 120.0


@runtime_checkable
class ModelProvider(Protocol):
    info: ProviderInfo

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn: ...

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult: ...


def request_fingerprint(system: SystemPrompt, conversation: Conversation, tools: list[ToolSpec],
                        schema_name: str | None) -> str:
    """Stable digest of one provider request; used by the trace and by replay cassettes."""
    payload = {
        "system": system.text,
        "conversation": [m.model_dump(mode="json") for m in conversation],
        "tools": [t.model_dump(mode="json") for t in tools],
        "schema": schema_name,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
