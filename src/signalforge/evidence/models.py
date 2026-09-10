"""Evidence item: one registered tool result or resource read."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^EVD-\d{6}$")
    investigation_id: str
    sequence: int = Field(ge=1, description="Acquisition order within the investigation.")
    acquired_at: datetime
    source_kind: Literal["tool", "resource"]
    source_name: str = Field(description="Tool name or resource URI.")
    arguments: dict[str, Any] = {}
    ok: bool
    error: str | None = None
    result_kind: str | None = Field(default=None, description="Envelope kind, 'document', or None on error.")
    source_ids: list[str] = Field(default_factory=list, description="World record IDs this item contains.")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payload: dict[str, Any] | None = None
    text: str | None = None
    latency_ms: float = Field(ge=0)

    def has_record(self, record_id: str) -> bool:
        return record_id in self.source_ids
