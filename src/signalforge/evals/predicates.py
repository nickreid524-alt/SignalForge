"""Does the gathered evidence satisfy a scenario predicate?

Unlike ``GroundTruth.evaluate`` (which asks the *world*), these functions ask the
*investigation*: did the agent actually retrieve evidence that covers the
decisive fact? Predicates are structural (record ids, windows, nodes, metrics,
patterns), never string matching on prose.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta

from signalforge.evidence.models import EvidenceItem
from signalforge.scenarios.models import EvidencePredicate

RECORD_KIND_TOOL = {"deployments": "deployments", "config_changes": "config_changes", "alerts": "alerts"}
RECORD_KIND_SCOPE_KEY = {"deployments": "service", "config_changes": "node", "alerts": "node"}


def _dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _window(item: EvidenceItem) -> tuple[datetime | None, datetime | None]:
    payload = item.payload or {}
    return _dt(payload.get("window_start")), _dt(payload.get("window_end"))


def _covers(item: EvidenceItem, start: datetime, end: datetime) -> bool:
    ws, we = _window(item)
    return ws is not None and we is not None and ws <= start and end <= we


def _overlaps(item: EvidenceItem, start: datetime, end: datetime) -> bool:
    ws, we = _window(item)
    return ws is not None and we is not None and ws < end and start < we


def _ok_items(items: Iterable[EvidenceItem], kind: str | None = None) -> list[EvidenceItem]:
    return [i for i in items if i.ok and (kind is None or i.result_kind == kind)]


def evidence_satisfies(pred: EvidencePredicate, items: Iterable[EvidenceItem], manifest: Mapping[str, str]) -> bool:
    items = list(items)
    if pred.kind == "record":
        record_id = manifest.get(pred.handle or "")
        return bool(record_id) and any(record_id in i.source_ids for i in _ok_items(items))

    if pred.kind in ("metric_change", "metric_stable", "metric_level"):
        assert pred.node and pred.metric and pred.at
        for item in _ok_items(items, "metric_series"):
            payload = item.payload or {}
            if payload.get("node") != pred.node or payload.get("metric") != pred.metric:
                continue
            if pred.dimension is not None and payload.get("dimension") != pred.dimension:
                continue
            if _covers(item, pred.at, pred.at):
                return True
        return False

    if pred.kind == "log_pattern":
        assert pred.node and pred.pattern_id and pred.window_start and pred.window_end
        for item in _ok_items(items, "log_query"):
            payload = item.payload or {}
            if payload.get("node") != pred.node or not _overlaps(item, pred.window_start, pred.window_end):
                continue
            patterns = {p.get("pattern_id") for p in payload.get("top_patterns", []) if isinstance(p, dict)}
            patterns |= {e.get("pattern_id") for e in payload.get("entries", []) if isinstance(e, dict)}
            if pred.pattern_id in patterns:
                return True
        return False

    if pred.kind == "node_status":
        assert pred.node and pred.at
        for item in _ok_items(items, "service_health"):
            payload = item.payload or {}
            health = payload.get("health") or {}
            as_of = _dt(payload.get("as_of"))
            if health.get("node") == pred.node and as_of is not None and abs(as_of - pred.at) <= timedelta(hours=3):
                return True
        return False

    if pred.kind == "document":
        assert pred.document_id
        for item in _ok_items(items):
            if any(sid == pred.document_id or sid.split("#", 1)[0] == pred.document_id for sid in item.source_ids):
                return True
        return False

    if pred.kind == "dependency_edge":
        assert pred.edge_id
        return any(pred.edge_id in i.source_ids for i in _ok_items(items))

    if pred.kind == "absence":
        assert pred.node and pred.record_kind and pred.window_start and pred.window_end
        scope_key = RECORD_KIND_SCOPE_KEY[pred.record_kind]
        for item in _ok_items(items, RECORD_KIND_TOOL[pred.record_kind]):
            query = (item.payload or {}).get("query") or {}
            scope = query.get(scope_key)
            if scope not in (None, pred.node):
                continue
            if _covers(item, pred.window_start, pred.window_end):
                return True
        return False

    return False
