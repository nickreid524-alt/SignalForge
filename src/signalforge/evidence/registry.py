"""The evidence registry assigns EVD IDs and hashes payloads. One registry per investigation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from signalforge.evidence.models import EvidenceItem


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_hash(payload: Any, text: str | None) -> str:
    digest = hashlib.sha256(canonical_json({"payload": payload, "text": text}).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


class EvidenceRegistry:
    def __init__(self, investigation_id: str, clock: Callable[[], datetime] | None = None) -> None:
        if not investigation_id:
            raise ValueError("investigation_id is required")
        self.investigation_id = investigation_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._items: dict[str, EvidenceItem] = {}
        self._sequence = 0

    # ------------------------------------------------------------------ registration
    def _next_id(self) -> tuple[str, int]:
        self._sequence += 1
        return f"EVD-{self._sequence:06d}", self._sequence

    def register_tool_result(self, *, name: str, arguments: dict[str, Any], payload: dict[str, Any] | None,
                             text: str | None, ok: bool, error: str | None, latency_ms: float) -> EvidenceItem:
        evidence_id, seq = self._next_id()
        source_ids = _string_list(payload.get("source_ids")) if isinstance(payload, dict) else []
        result_kind = payload.get("kind") if isinstance(payload, dict) and isinstance(payload.get("kind"), str) else None
        item = EvidenceItem(
            evidence_id=evidence_id, investigation_id=self.investigation_id, sequence=seq,
            acquired_at=self._clock(), source_kind="tool", source_name=name, arguments=dict(arguments), ok=ok,
            error=error, result_kind=result_kind if ok else None, source_ids=source_ids if ok else [],
            content_hash=content_hash(payload, text), payload=payload, text=text, latency_ms=latency_ms,
        )
        self._items[evidence_id] = item
        return item

    def register_resource(self, *, uri: str, text: str | None, payload: dict[str, Any] | None,
                          source_ids: list[str], ok: bool, error: str | None, latency_ms: float) -> EvidenceItem:
        evidence_id, seq = self._next_id()
        item = EvidenceItem(
            evidence_id=evidence_id, investigation_id=self.investigation_id, sequence=seq,
            acquired_at=self._clock(), source_kind="resource", source_name=uri, arguments={"uri": uri}, ok=ok,
            error=error, result_kind="document" if ok else None, source_ids=list(source_ids) if ok else [],
            content_hash=content_hash(payload, text), payload=payload, text=text, latency_ms=latency_ms,
        )
        self._items[evidence_id] = item
        return item

    # ------------------------------------------------------------------ lookup
    def get(self, evidence_id: str) -> EvidenceItem | None:
        return self._items.get(evidence_id)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[EvidenceItem]:
        return sorted(self._items.values(), key=lambda i: i.sequence)

    def ids(self) -> list[str]:
        return [i.evidence_id for i in self.items()]

    def find_by_record(self, record_id: str) -> list[EvidenceItem]:
        return [i for i in self.items() if i.has_record(record_id)]

    def all_record_ids(self) -> set[str]:
        return {rid for item in self._items.values() for rid in item.source_ids}
