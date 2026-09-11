"""The application event contract: stable, UI-facing facts about a running investigation.

This package is deliberately independent of both the orchestrator and the API:

    orchestration --emits--> events <--reads-- api

Events are **not** trace rows. The audit trace (``signalforge.audit``) is a deep
internal record whose schema is free to change; these events are the public
contract a frontend may depend on. Nothing here imports orchestration, MCP or
HTTP, and payload models never carry provider-native objects or hidden
reasoning.
"""

from signalforge.events.models import (
    EVENT_PAYLOADS,
    TERMINAL_EVENTS,
    EventType,
    InvestigationEvent,
)
from signalforge.events.store import EventEmitter, EventStore, NullEmitter

__all__ = [
    "EVENT_PAYLOADS",
    "TERMINAL_EVENTS",
    "EventEmitter",
    "EventStore",
    "EventType",
    "InvestigationEvent",
    "NullEmitter",
]
