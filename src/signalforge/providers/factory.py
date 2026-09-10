"""Provider selection and status from configuration/environment.

    SIGNALFORGE_PROVIDER              scripted | replay | anthropic | openai   (default: scripted)
    SIGNALFORGE_ANTHROPIC_MODEL       required for anthropic  (with ANTHROPIC_API_KEY)
    SIGNALFORGE_OPENAI_MODEL          required for openai     (with OPENAI_API_KEY)
    SIGNALFORGE_MAX_OUTPUT_TOKENS     default 8192
    SIGNALFORGE_PROVIDER_TIMEOUT      seconds, default 120
    SIGNALFORGE_EFFORT                optional reasoning effort passed to live providers

Model ids are never hardcoded here; they come from the environment so the
project does not carry a stale model name.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from signalforge.providers.base import ModelProvider, ProviderError
from signalforge.providers.errors import ProviderFailure

PROVIDER_CHOICES: tuple[str, ...] = ("scripted", "replay", "anthropic", "openai")
LIVE_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai"})

ENV_PROVIDER = "SIGNALFORGE_PROVIDER"
ENV_ANTHROPIC_MODEL = "SIGNALFORGE_ANTHROPIC_MODEL"
ENV_OPENAI_MODEL = "SIGNALFORGE_OPENAI_MODEL"
ENV_MAX_OUTPUT_TOKENS = "SIGNALFORGE_MAX_OUTPUT_TOKENS"
ENV_TIMEOUT = "SIGNALFORGE_PROVIDER_TIMEOUT"
ENV_EFFORT = "SIGNALFORGE_EFFORT"


@dataclass(frozen=True)
class ProviderSettings:
    anthropic_model: str | None = None
    openai_model: str | None = None
    max_output_tokens: int = 8192
    timeout_seconds: float = 120.0
    effort: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProviderSettings:
        env = os.environ if env is None else env

        def _int(name: str, default: int) -> int:
            try:
                return int(env.get(name, default))
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(env.get(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            anthropic_model=env.get(ENV_ANTHROPIC_MODEL) or None,
            openai_model=env.get(ENV_OPENAI_MODEL) or None,
            max_output_tokens=_int(ENV_MAX_OUTPUT_TOKENS, 8192),
            timeout_seconds=_float(ENV_TIMEOUT, 120.0),
            effort=env.get(ENV_EFFORT) or None,
        )


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    mode: str
    uses_live_api: bool
    sdk_installed: bool | None
    sdk_version: str | None
    credentials_present: bool | None
    model: str | None
    ready: bool
    note: str


def _sdk_installed(package: str) -> tuple[bool, str | None]:
    try:
        spec = importlib.util.find_spec(package)
    except (ValueError, ImportError):
        spec = None
    if spec is None:
        return False, None
    try:
        from importlib import metadata

        return True, metadata.version(package)
    except Exception:
        return True, None


def default_provider_name(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    name = (env.get(ENV_PROVIDER) or "scripted").strip().lower()
    return name if name in PROVIDER_CHOICES else "scripted"


def provider_status(name: str, settings: ProviderSettings | None = None, env: Mapping[str, str] | None = None) -> ProviderStatus:
    env = os.environ if env is None else env
    settings = settings or ProviderSettings.from_env(env)
    if name == "scripted":
        return ProviderStatus("scripted", "scripted", False, None, None, None, "playbook-v1", True,
                              "deterministic demonstration; no LLM, no network")
    if name == "replay":
        return ProviderStatus("replay", "replay", False, None, None, None, None, True,
                              "replays a recorded cassette; no LLM, no network (needs --cassette)")
    if name == "anthropic":
        from signalforge.providers import anthropic_provider as ap

        installed, version = _sdk_installed(ap.SDK_PACKAGE)
        creds = ap.credentials_present(env)
        model = settings.anthropic_model
        ready = installed and creds and bool(model)
        missing = [x for x, ok in (("pip install \"signalforge[anthropic]\"", installed), (ap.KEY_ENVS[0], creds), (ap.MODEL_ENV, bool(model))) if not ok]
        return ProviderStatus("anthropic", "live", True, installed, version, creds, model, ready,
                              "ready" if ready else "missing: " + ", ".join(missing))
    if name == "openai":
        from signalforge.providers import openai_provider as op

        installed, version = _sdk_installed(op.SDK_PACKAGE)
        creds = op.credentials_present(env)
        model = settings.openai_model
        ready = installed and creds and bool(model)
        missing = [x for x, ok in (("pip install \"signalforge[openai]\"", installed), (op.KEY_ENVS[0], creds), (op.MODEL_ENV, bool(model))) if not ok]
        return ProviderStatus("openai", "live", True, installed, version, creds, model, ready,
                              "ready" if ready else "missing: " + ", ".join(missing))
    raise ProviderError(f"unknown provider {name!r}; choices: {', '.join(PROVIDER_CHOICES)}")


def all_provider_statuses(settings: ProviderSettings | None = None, env: Mapping[str, str] | None = None) -> list[ProviderStatus]:
    return [provider_status(name, settings, env) for name in PROVIDER_CHOICES]


def create_provider(name: str, *, cassette: str | None = None, settings: ProviderSettings | None = None,
                    env: Mapping[str, str] | None = None, client: Any | None = None) -> ModelProvider:
    """Build a provider. Live providers validate configuration eagerly and fail with a setup error, never a stack trace."""
    env = os.environ if env is None else env
    settings = settings or ProviderSettings.from_env(env)
    if name == "scripted":
        from signalforge.providers.scripted import ScriptedDemoProvider

        return ScriptedDemoProvider()
    if name == "replay":
        from signalforge.providers.replay import ReplayProvider

        if not cassette:
            raise ProviderError("the replay provider needs --cassette <path>")
        return ReplayProvider.from_file(cassette)
    if name == "anthropic":
        from signalforge.providers.anthropic_provider import AnthropicProvider, AnthropicSettings

        if not settings.anthropic_model:
            raise ProviderFailure("missing_model", f"set {ENV_ANTHROPIC_MODEL} to the Claude model id to use", provider="anthropic")
        return AnthropicProvider(AnthropicSettings(model=settings.anthropic_model, max_output_tokens=settings.max_output_tokens,
                                                   timeout_seconds=settings.timeout_seconds, effort=settings.effort),
                                 client=client, env=env)
    if name == "openai":
        from signalforge.providers.openai_provider import OpenAIProvider, OpenAISettings

        if not settings.openai_model:
            raise ProviderFailure("missing_model", f"set {ENV_OPENAI_MODEL} to the OpenAI model id to use", provider="openai")
        return OpenAIProvider(OpenAISettings(model=settings.openai_model, max_output_tokens=settings.max_output_tokens,
                                             timeout_seconds=settings.timeout_seconds, reasoning_effort=settings.effort),
                              client=client, env=env)
    raise ProviderError(f"unknown provider {name!r}; choices: {', '.join(PROVIDER_CHOICES)}")
