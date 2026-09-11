"""Background execution of investigations, with a bound on how many run at once.

The runner owns scheduling and nothing else. It creates a provider through the existing factory,
opens an MCP client, and hands both to the existing :class:`Investigator`. There is no second agent
loop here: if the orchestration logic changed, this file would not.

Lifecycle is explicit. Every task is tracked, the queue is bounded, and :meth:`aclose` cancels and
awaits everything, so shutting the app down cannot leave an investigation running.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from signalforge.api.errors import ApiError
from signalforge.api.schemas import BUDGET_PROFILES
from signalforge.audit.store import TraceStore
from signalforge.events import EventType
from signalforge.events import models as ev
from signalforge.events.store import EventEmitter, EventStore
from signalforge.mcp_client.client import OpsClient
from signalforge.orchestration.budget import InvestigationBudget
from signalforge.orchestration.investigator import InvestigationResult, Investigator
from signalforge.orchestration.state import InvestigationState
from signalforge.providers.base import ProviderError, ProviderInfo
from signalforge.providers.factory import create_provider

QUEUED = "queued"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class InvestigationRecord:
    """What the API knows about one investigation, live or finished."""

    id: str
    incident_id: str
    provider_name: str
    budget_profile: str
    created_at: datetime
    provider_mode: str = "scripted"
    uses_live_api: bool = False
    model: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    #: The live orchestration state, available as soon as the investigation starts.
    state: InvestigationState | None = None
    result: InvestigationResult | None = None
    error: str | None = None
    budget: InvestigationBudget = field(default_factory=InvestigationBudget)

    @property
    def status(self) -> str:
        if self.state is not None:
            return self.state.status.value
        return "failed" if self.error else QUEUED

    @property
    def terminal(self) -> bool:
        return self.ended_at is not None


class InvestigationRunner:
    def __init__(self, *, server: Any, trace: TraceStore, events: EventStore,
                 provider_factory: Callable[..., Any] = create_provider, max_concurrent: int = 2,
                 max_queued: int = 16, allow_live_providers: bool = False,
                 client_factory: Callable[[Any], Any] | None = None) -> None:
        self.server = server
        self.trace = trace
        self.events = events
        self.provider_factory = provider_factory
        self.max_concurrent = max_concurrent
        self.max_queued = max_queued
        self.allow_live_providers = allow_live_providers
        self.client_factory = client_factory or (lambda srv: OpsClient.in_memory(srv))
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._records: dict[str, InvestigationRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        #: Observability for the concurrency bound; asserted in tests.
        self.active = 0
        self.peak_concurrency = 0

    # ------------------------------------------------------------------ lifecycle
    async def aclose(self) -> None:
        """Cancel and await every running investigation. Nothing survives the app."""
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()

    @property
    def pending(self) -> int:
        return sum(1 for r in self._records.values() if not r.terminal)

    # ------------------------------------------------------------------ queries
    def get(self, investigation_id: str) -> InvestigationRecord | None:
        return self._records.get(investigation_id)

    def list(self) -> list[InvestigationRecord]:
        return sorted(self._records.values(), key=lambda r: (r.created_at, r.id))

    # ------------------------------------------------------------------ submission
    def submit(self, incident_id: str, *, provider_name: str = "scripted", budget_profile: str = "default",
               incident_title: str = "", affected_service: str = "") -> InvestigationRecord:
        if self._closed:
            raise ApiError("investigation_conflict", "the server is shutting down")
        if self.pending >= self.max_queued:
            raise ApiError("too_many_investigations",
                           f"{self.pending} investigations are already queued or running (limit {self.max_queued})")
        provider = self._build_provider(provider_name)
        info: ProviderInfo = provider.info
        budget = BUDGET_PROFILES[budget_profile]
        record = InvestigationRecord(
            id=f"inv-{incident_id.lower()}-{uuid.uuid4().hex[:8]}", incident_id=incident_id,
            provider_name=info.name, budget_profile=budget_profile, created_at=_now(), provider_mode=info.mode,
            uses_live_api=info.uses_live_api, model=info.model, budget=budget,
        )
        self._records[record.id] = record
        emitter = EventEmitter(record.id, self.events)
        emitter.emit(EventType.INVESTIGATION_CREATED, ev.InvestigationCreated(
            incident_id=incident_id, incident_title=incident_title, affected_service=affected_service,
            provider=info.name, provider_mode=info.mode, uses_live_api=info.uses_live_api, model=info.model,
            budget_profile=budget_profile))
        task = asyncio.create_task(self._run(record, provider, emitter), name=f"investigation:{record.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record

    def _build_provider(self, provider_name: str) -> Any:
        """Create the provider now, so a misconfiguration is a 409 on POST, not a failed background task."""
        if provider_name not in ("scripted", "replay", "anthropic", "openai"):
            raise ApiError("invalid_request", f"unknown provider {provider_name!r}")
        if provider_name == "replay":
            raise ApiError("provider_unavailable",
                           "the replay provider needs a cassette; it is available through the CLI, not the API")
        if provider_name in ("anthropic", "openai") and not self.allow_live_providers:
            raise ApiError("provider_unavailable",
                           f"live provider {provider_name!r} is disabled on this server; start it with "
                           "SIGNALFORGE_API_ALLOW_LIVE=1 and the vendor credentials in the environment")
        try:
            return self.provider_factory(provider_name)
        except ProviderError as exc:
            # Includes ProviderFailure: missing SDK, key or model. The message names variables, never values.
            raise ApiError("provider_unavailable", f"provider {provider_name!r} is not available: {exc}") from None

    # ------------------------------------------------------------------ execution
    async def _run(self, record: InvestigationRecord, provider: Any, emitter: EventEmitter) -> None:
        try:
            async with self._semaphore:
                self.active += 1
                self.peak_concurrency = max(self.peak_concurrency, self.active)
                record.started_at = _now()
                try:
                    async with self.client_factory(self.server) as client:
                        investigator = Investigator(provider, client, trace=self.trace, budget=record.budget,
                                                    events=emitter)
                        # on_state hands the live state object over the moment the engine creates it,
                        # so GET /api/investigations/{id} reports real progress while the run continues.
                        record.result = await investigator.run(
                            record.incident_id, investigation_id=record.id,
                            on_state=lambda state: setattr(record, "state", state))
                finally:
                    self.active -= 1
        except asyncio.CancelledError:
            record.error = "cancelled: the server shut down before this investigation finished"
            emitter.emit(EventType.INVESTIGATION_FAILED, ev.InvestigationFailed(
                terminal_status="failed", message=record.error, category="cancelled"))
            raise
        except Exception as exc:
            record.error = f"{exc.__class__.__name__}: {exc}"
            emitter.emit(EventType.INVESTIGATION_FAILED, ev.InvestigationFailed(
                terminal_status="failed", message=record.error, category="runner_error"))
        finally:
            if record.result is not None:
                record.state = record.result.state
            record.ended_at = _now()
