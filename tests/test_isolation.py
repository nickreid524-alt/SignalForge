"""Ground truth must be unreachable from anything the MCP server can touch."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from signalforge.scenarios.catalogue import SCENARIOS
from signalforge.world.models import (
    Alert,
    ConfigChange,
    Deployment,
    LogEffect,
    MetricEffect,
    OpenIncident,
    StatusEffect,
    WorldSnapshot,
)
from signalforge.world.repository import WorldRepository

SRC = Path(__file__).resolve().parents[1] / "src" / "signalforge"
SERVER_FACING_PACKAGES = ("world", "mcp_server", "retrieval", "mcp_client", "evidence", "config.py")
FORBIDDEN_NAMES = ("root_cause", "decisive", "red_herring", "misleading", "expected_useful", "unacceptable",
                   "ground_truth", "culprit", "manifest", "handle")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_server_facing_code_never_imports_scenarios():
    for entry in SERVER_FACING_PACKAGES:
        target = SRC / entry
        files = [target] if target.is_file() else list(target.rglob("*.py"))
        assert files
        for file in files:
            offenders = {m for m in _imports(file) if m.startswith("signalforge.scenarios")}
            assert not offenders, f"{file.relative_to(SRC)} imports ground truth: {offenders}"


def test_snapshot_schema_has_no_ground_truth_fields():
    for model in (WorldSnapshot, OpenIncident, Deployment, ConfigChange, Alert, MetricEffect, LogEffect, StatusEffect):
        for name in model.model_fields:
            assert not any(bad in name for bad in FORBIDDEN_NAMES), (model.__name__, name)
    assert "manifest" not in WorldSnapshot.model_fields


def test_repository_exposes_no_ground_truth(repo: WorldRepository):
    public = [n for n in dir(repo) if not n.startswith("_")]
    for name in public:
        assert not any(bad in name.lower() for bad in FORBIDDEN_NAMES), name
    assert not hasattr(repo, "manifest")
    assert not hasattr(repo.snapshot, "manifest")


def test_snapshot_records_do_not_contain_root_cause_statements_or_handles(generated):
    """Serialize everything the server can return except the document corpus, and check for leakage."""
    s = generated.snapshot
    payload = json.dumps({
        "incidents": [i.model_dump(mode="json") for i in s.open_incidents],
        "deployments": [d.model_dump(mode="json") for d in s.deployments],
        "config": [c.model_dump(mode="json") for c in s.config_changes],
        "alerts": [a.model_dump(mode="json") for a in s.alerts],
        "effects": [e.model_dump(mode="json") for e in [*s.metric_effects, *s.log_effects, *s.status_effects]],
    }).lower()
    for spec in SCENARIOS:
        assert spec.root_cause_statement.lower() not in payload, spec.id
        assert spec.id.lower() not in payload, spec.id  # SCN-xx identifiers never appear in world records
    for handle in generated.manifest:
        assert handle.lower() not in payload, handle
    for bad in ("root cause", "red herring", "decoy", "ground truth", "decisive"):
        assert bad not in payload, bad


def test_open_incident_resource_has_no_cause(generated):
    """Reading incident://INC-2026-01xx must not reveal anything beyond the paging text."""
    from signalforge.config import DATA_NOTICE
    from signalforge.mcp_server.resources import (
        register_resources,  # noqa: F401  (ensures module imports cleanly)
    )

    for inc in generated.snapshot.open_incidents:
        text = f"{inc.title} {inc.description}".lower()
        assert "root cause" not in text
        assert "decoy" not in text
    assert "instructions" in DATA_NOTICE.lower()
