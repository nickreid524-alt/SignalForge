"""Provider selection by name. Live vendors arrive in a later phase; asking for one fails loudly."""

from __future__ import annotations

from signalforge.providers.base import ModelProvider, ProviderError
from signalforge.providers.replay import ReplayProvider
from signalforge.providers.scripted import ScriptedDemoProvider

PROVIDER_CHOICES: tuple[str, ...] = ("scripted", "replay", "anthropic", "openai")


def create_provider(name: str, *, cassette: str | None = None) -> ModelProvider:
    if name == "scripted":
        return ScriptedDemoProvider()
    if name == "replay":
        if not cassette:
            raise ProviderError("the replay provider needs --cassette <path>")
        return ReplayProvider.from_file(cassette)
    if name in ("anthropic", "openai"):
        raise ProviderError(f"provider {name!r} is not available in this phase (planned: live adapters behind the same "
                            f"boundary; API keys will be read from environment variables only)")
    raise ProviderError(f"unknown provider {name!r}; choices: {', '.join(PROVIDER_CHOICES)}")
