"""Model providers behind the neutral boundary: scripted demonstration, replay, and (later) live vendors.

Only the boundary types are imported eagerly; concrete providers load lazily so
that orchestration modules can import ``signalforge.providers.base`` without
pulling providers (and their dependencies) into the import graph.
"""

from __future__ import annotations

from typing import Any

from signalforge.providers.base import (
    EVIDENCE_HEADER_PREFIX,
    AssistantMessage,
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelProvider,
    ModelTurn,
    ProviderCapabilities,
    ProviderError,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolRequest,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
    request_fingerprint,
)

_LAZY = {
    "ScriptedDemoProvider": ("signalforge.providers.scripted", "ScriptedDemoProvider"),
    "ReplayProvider": ("signalforge.providers.replay", "ReplayProvider"),
    "RecordingProvider": ("signalforge.providers.replay", "RecordingProvider"),
    "ReplayMismatch": ("signalforge.providers.replay", "ReplayMismatch"),
    "Cassette": ("signalforge.providers.replay", "Cassette"),
    "create_provider": ("signalforge.providers.factory", "create_provider"),
    "PROVIDER_CHOICES": ("signalforge.providers.factory", "PROVIDER_CHOICES"),
    "ProviderSettings": ("signalforge.providers.factory", "ProviderSettings"),
    "provider_status": ("signalforge.providers.factory", "provider_status"),
    "AnthropicProvider": ("signalforge.providers.anthropic_provider", "AnthropicProvider"),
    "OpenAIProvider": ("signalforge.providers.openai_provider", "OpenAIProvider"),
    "ProviderFailure": ("signalforge.providers.errors", "ProviderFailure"),
    "classify_exception": ("signalforge.providers.errors", "classify_exception"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    module = __import__(target[0], fromlist=[target[1]])
    return getattr(module, target[1])


__all__ = [
    "EVIDENCE_HEADER_PREFIX",
    "PROVIDER_CHOICES",
    "AnthropicProvider",
    "AssistantMessage",
    "Cassette",
    "Conversation",
    "GenerationConfig",
    "InvestigationContext",
    "ModelProvider",
    "ModelTurn",
    "OpenAIProvider",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderFailure",
    "ProviderInfo",
    "ProviderSettings",
    "RecordingProvider",
    "ReplayMismatch",
    "ReplayProvider",
    "ScriptedDemoProvider",
    "StructuredResult",
    "SystemPrompt",
    "ToolRequest",
    "ToolResult",
    "ToolResultsMessage",
    "ToolSpec",
    "UserMessage",
    "classify_exception",
    "create_provider",
    "provider_status",
    "request_fingerprint",
]
