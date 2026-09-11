"""Shared helpers for the API tests. No network beyond loopback, no model API, no credentials."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import anyio
from pydantic import BaseModel
from starlette.testclient import TestClient

from signalforge.api.app import create_app
from signalforge.api.config import ApiSettings
from signalforge.api.services import AppServices
from signalforge.audit.store import TraceStore
from signalforge.events.store import EventStore
from signalforge.providers.base import (
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelTurn,
    ProviderCapabilities,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolSpec,
)
from signalforge.providers.errors import ProviderFailure
from signalforge.providers.scripted import ScriptedDemoProvider

TERMINAL = ("investigation.completed", "investigation.failed")


@dataclass
class Harness:
    client: TestClient
    services: AppServices
    settings: ApiSettings

    @property
    def runner(self) -> Any:
        return self.services.runner


@contextmanager
def api_harness(snapshot: Any, *, settings: ApiSettings | None = None,
                provider_factory: Any = None) -> Iterator[Harness]:
    """A live API backed by in-memory stores and the shared synthetic world."""
    settings = settings or ApiSettings(trace_db=":memory:", event_db=":memory:", max_concurrency=2)
    services = AppServices.build(settings, snapshot=snapshot, trace=TraceStore(":memory:"),
                                 events=EventStore(":memory:"), provider_factory=provider_factory)
    app = create_app(settings=settings, services=services)
    with TestClient(app) as client:
        yield Harness(client=client, services=services, settings=settings)


# ---------------------------------------------------------------------- driving investigations


def start(harness: Harness, incident_id: str = "INC-2026-0106", **body: Any) -> str:
    response = harness.client.post("/api/investigations", json={"incident_id": incident_id, **body})
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse an SSE body into frames of {id, event, data}."""
    frames: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith("id:"):
            current["id"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line.split(":", 1)[1].strip())
        elif line == "" and current.get("event"):
            frames.append(current)
            current = {}
    if current.get("event"):
        frames.append(current)
    return frames


def stream_until(harness: Harness, investigation_id: str, *, after: int | None = None,
                 stop_after: int | None = None, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Open the SSE stream and read frames until the terminal event (or ``stop_after`` frames)."""
    url = f"/api/investigations/{investigation_id}/events"
    if after is not None:
        url += f"?after={after}"
    frames: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    with harness.client.stream("GET", url, headers=headers or {}) as response:
        assert response.status_code == 200, response.status_code
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("id:"):
                current["id"] = int(line.split(":", 1)[1].strip())
            elif line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line.split(":", 1)[1].strip())
                frames.append(current)
                terminal = current["event"] in TERMINAL
                current = {}
                if terminal or (stop_after is not None and len(frames) >= stop_after):
                    break
    return frames


def wait_for_terminal(harness: Harness, investigation_id: str, timeout: float = 120.0) -> dict[str, Any]:
    """Block until the investigation reaches a terminal state, then return its detail document."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = harness.client.get(f"/api/investigations/{investigation_id}").json()
        if detail.get("terminal"):
            return detail
        time.sleep(0.02)
    raise AssertionError(f"investigation {investigation_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------- providers for tests


class SlowScriptedProvider:
    """The scripted provider with a delay, so concurrency is observable."""

    def __init__(self, delay: float = 0.15) -> None:
        self._inner = ScriptedDemoProvider()
        self._delay = delay

    @property
    def info(self) -> ProviderInfo:
        return self._inner.info

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        await anyio.sleep(self._delay)
        return await self._inner.complete(system, conversation, tools=tools, context=context, config=config)

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *,
                                  schema: type[BaseModel], context: InvestigationContext,
                                  config: GenerationConfig) -> StructuredResult:
        await anyio.sleep(self._delay)
        return await self._inner.generate_structured(system, conversation, schema=schema, context=context,
                                                     config=config)


FAILING_INFO = ProviderInfo(name="failing-test-double", model="none", mode="scripted", uses_llm=False,
                            description="Test double that always fails with a non-retryable provider error.",
                            capabilities=ProviderCapabilities())


class FailingProvider:
    """Fails the way a live provider with a bad credential would: normalised and non-retryable."""

    info = FAILING_INFO

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        raise ProviderFailure("authentication", "invalid credential", provider="failing-test-double")

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *,
                                  schema: type[BaseModel], context: InvestigationContext,
                                  config: GenerationConfig) -> StructuredResult:
        raise ProviderFailure("authentication", "invalid credential", provider="failing-test-double")
