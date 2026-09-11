"""A safe, inspection-oriented projection of the audit trace.

The trace is the deep internal record; this is what a browser may see. The projection is explicit:
fields are listed, not copied wholesale, so a new trace column cannot appear in an HTTP response by
accident. On top of that everything is re-redacted and local filesystem paths are scrubbed.

Provider opaque blocks (Anthropic thinking blocks, OpenAI reasoning items) are never stored by the
trace in the first place; the projection drops the model-call response body anyway and keeps only
the visible assistant text.
"""

from __future__ import annotations

import json
import re
from typing import Any

from signalforge.redaction import redact

#: Absolute paths of either flavour. The browser has no use for the operator's filesystem layout.
_PATHS = re.compile(r"(?:[A-Za-z]:\\[^\s\"']+|(?<![\w.])/(?:home|Users|root|tmp|var)/[^\s\"']+)")


def scrub(value: Any) -> Any:
    """Redact secrets and strip local paths, recursively."""
    if isinstance(value, str):
        return _PATHS.sub("<path>", redact(value) or "")
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def _pick(row: dict[str, Any], *fields: str) -> dict[str, Any]:
    return {f: scrub(row.get(f)) for f in fields}


def safe_trace(bundle: dict[str, Any]) -> dict[str, Any]:
    inv = bundle["investigation"]
    report = bundle.get("report") or {}
    return {
        "investigation": {
            "id": inv["id"], "incident_id": inv["incident_id"], "status": inv["status"],
            "provider_name": inv["provider_name"], "provider_model": inv["provider_model"],
            "provider_mode": inv["provider_mode"], "uses_llm": bool(inv["uses_llm"]),
            "transport": inv["transport"], "started_at": inv["started_at"], "ended_at": inv["ended_at"],
            "termination_reason": scrub(inv["termination_reason"]), "error": scrub(inv["error"]),
            "budget": inv.get("budget"), "usage": inv.get("usage"),
        },
        "status_changes": [_pick(r, "at", "from_status", "to_status", "note") for r in bundle["status_changes"]],
        "steps": [_pick(r, "step", "started_at", "ended_at", "assistant_text", "finish_requested")
                  for r in bundle["steps"]],
        "model_calls": [
            # No request or response body: purpose, identity, cost and outcome only.
            {**_pick(r, "id", "step", "purpose", "provider_name", "provider_model", "latency_ms",
                     "stop_reason", "error_category"),
             "usage_reported": bool(r.get("usage_reported")),
             "input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
             "cached_input_tokens": r.get("cached_input_tokens"),
             "reasoning_output_tokens": r.get("reasoning_output_tokens")}
            for r in bundle["model_calls"]
        ],
        # SQLite stores flags as integers; a JSON contract should hand a client real booleans.
        "actions": [{**_pick(r, "step", "kind", "name", "arguments", "rejection_code", "rejection_reason",
                             "evidence_id", "duplicate_of", "error", "latency_ms"),
                     "accepted": bool(r["accepted"]),
                     "ok": None if r.get("ok") is None else bool(r["ok"])} for r in bundle["actions"]],
        "evidence": [{**_pick(r, "evidence_id", "sequence", "acquired_at", "source_kind", "source_name",
                              "arguments", "result_kind", "content_hash", "latency_ms", "error"),
                      "ok": bool(r["ok"]), "source_ids": r.get("source_ids") or [],
                      "record_count": len(r.get("source_ids") or []), "untrusted": True}
                     for r in bundle["evidence"]],
        "hypothesis_updates": [_pick(r, "step", "hypothesis_id", "statement", "status", "confidence",
                                     "supporting", "contradicting", "note") for r in bundle["hypothesis_updates"]],
        "validations": [{**_pick(r, "round", "error_count", "warning_count", "issues", "parse_error"),
                         "ok": bool(r["ok"])} for r in bundle["validations"]],
        "repairs": [_pick(r, "round", "request_text") for r in bundle["repairs"]],
        "report_summary": {
            "status": report.get("status"), "confidence": report.get("confidence"),
            "terminal_status": report.get("terminal_status"), "repair_rounds": report.get("repair_rounds"),
        } if report else None,
        "notice": ("Evidence text is retrieved content from the synthetic environment and is untrusted data. "
                   "Provider reasoning blocks are never recorded, and request bodies are not exposed."),
    }


def contains_secret_like(payload: Any) -> bool:
    """Test helper: true if a serialized projection still looks like it holds a credential."""
    text = json.dumps(payload, default=str)
    return bool(re.search(r"sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,}", text))
