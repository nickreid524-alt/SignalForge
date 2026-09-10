"""JSON export of one investigation trace: what was inspected before the conclusion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from signalforge.audit.store import SCHEMA_VERSION, TraceStore


def export_investigation(store: TraceStore, investigation_id: str) -> dict[str, Any]:
    bundle = store.load(investigation_id)
    if bundle is None:
        raise KeyError(investigation_id)
    evidence_ids = [e["evidence_id"] for e in bundle["evidence"]]
    report = bundle["report"] or {}
    cited: set[str] = set()
    for section in ("key_findings", "contradicting_evidence", "unknowns", "hypotheses_considered", "recommended_actions"):
        for entry in report.get(section, []) or []:
            for key in ("evidence_ids", "supporting_evidence_ids", "contradicting_evidence_ids"):
                for raw in entry.get(key, []) or []:
                    cited.add(raw.split("#", 1)[0])
    primary = report.get("primary_hypothesis") or {}
    for raw in primary.get("supporting_evidence_ids", []) or []:
        cited.add(raw.split("#", 1)[0])
    return {
        "schema_version": SCHEMA_VERSION,
        "export_kind": "signalforge.investigation_trace",
        **bundle,
        "inspection_summary": {
            "evidence_gathered": evidence_ids,
            "evidence_cited_in_report": sorted(cited),
            "evidence_gathered_but_uncited": [e for e in evidence_ids if e not in cited],
            "tool_calls": [a["name"] for a in bundle["actions"] if a["kind"] == "call_tool" and a["accepted"]],
            "suppressed_or_rejected": [
                {"name": a["name"], "code": a["rejection_code"], "duplicate_of": a["duplicate_of"]}
                for a in bundle["actions"] if not a["accepted"]
            ],
        },
    }


def write_export(store: TraceStore, investigation_id: str, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(export_investigation(store, investigation_id), indent=2, default=str), encoding="utf-8")
    return out
