"""Document corpus loader: runbooks and historical incident reviews (markdown + front matter)."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import resources

from signalforge.world.models import HistoricalIncident, Runbook


class CorpusError(ValueError):
    pass


def parse_front_matter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """Parse a minimal ``---`` front-matter block. Supports ``key: value`` and ``[a, b]`` lists."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CorpusError("document must start with a front-matter block")
    meta: dict[str, str | list[str]] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body = "\n".join(lines[index + 1:]).strip("\n")
            return meta, body
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise CorpusError(f"malformed front-matter line: {line!r}")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value.strip('"')
    raise CorpusError("unterminated front-matter block")


def _read_dir(package: str) -> list[tuple[str, str]]:
    root = resources.files(package)
    docs: list[tuple[str, str]] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            docs.append((entry.name, entry.read_text(encoding="utf-8")))
    return docs


def _str(meta: dict[str, str | list[str]], key: str, name: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value:
        raise CorpusError(f"{name}: front matter missing {key!r}")
    return value


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_runbooks() -> list[Runbook]:
    runbooks: list[Runbook] = []
    for name, text in _read_dir("signalforge.world.corpus.runbooks"):
        meta, body = parse_front_matter(text)
        tags = meta.get("tags", [])
        runbooks.append(Runbook(
            id=_str(meta, "id", name), title=_str(meta, "title", name), service=_str(meta, "service", name),
            tags=list(tags) if isinstance(tags, list) else [tags], updated=_str(meta, "updated", name), body=body,
        ))
    return sorted(runbooks, key=lambda r: r.id)


def load_historical_incidents() -> list[HistoricalIncident]:
    incidents: list[HistoricalIncident] = []
    for name, text in _read_dir("signalforge.world.corpus.incidents"):
        meta, body = parse_front_matter(text)
        incidents.append(HistoricalIncident(
            id=_str(meta, "id", name), title=_str(meta, "title", name), service=_str(meta, "service", name),
            severity=_str(meta, "severity", name),  # type: ignore[arg-type]
            occurred_at=_dt(_str(meta, "occurred_at", name)), resolved_at=_dt(_str(meta, "resolved_at", name)),
            category=_str(meta, "category", name), rca_summary=_str(meta, "rca_summary", name), body=body,
        ))
    return sorted(incidents, key=lambda i: i.id)
