"""Record/replay provider and the SQLite audit trace."""

from __future__ import annotations

import json
import sqlite3

import pytest

from signalforge.audit.export import export_investigation, write_export
from signalforge.audit.store import SCHEMA_VERSION, TraceStore, redact
from signalforge.mcp_client.client import OpsClient
from signalforge.orchestration.investigator import Investigator
from signalforge.orchestration.state import InvestigationStatus as S
from signalforge.providers.replay import Cassette, RecordingProvider, ReplayMismatch, ReplayProvider
from signalforge.providers.scripted import ScriptedDemoProvider

pytestmark = pytest.mark.anyio


async def _run(server, provider, incident, trace=None, investigation_id="t-replay"):
    trace = trace or TraceStore(":memory:")
    async with OpsClient.in_memory(server) as client:
        result = await Investigator(provider, client, trace=trace).run(incident, investigation_id=investigation_id)
    return result, trace


async def test_record_then_replay_reproduces_the_investigation(server, tmp_path):
    recorder = RecordingProvider(ScriptedDemoProvider())
    original, _ = await _run(server, recorder, "INC-2026-0106")
    assert original.state.status is S.COMPLETED
    cassette_path = recorder.cassette.save(tmp_path / "scn06.json")
    cassette = Cassette.load(cassette_path)
    assert len(cassette.entries) == original.state.usage.model_calls
    assert [e.kind for e in cassette.entries][-1] == "structured" and cassette.provider.mode == "scripted"

    replay = ReplayProvider.from_file(cassette_path)
    assert replay.info.mode == "replay" and replay.info.uses_llm is False
    replayed, _ = await _run(server, replay, "INC-2026-0106")
    assert replayed.state.status is S.COMPLETED
    assert replayed.report.model_dump(exclude={"provider", "investigation_duration_seconds"}) == \
           original.report.model_dump(exclude={"provider", "investigation_duration_seconds"})
    assert replay.cursor == len(cassette.entries)


async def test_replay_detects_divergence_and_exhaustion(server, tmp_path):
    recorder = RecordingProvider(ScriptedDemoProvider())
    await _run(server, recorder, "INC-2026-0106")
    cassette = recorder.cassette
    diverged, _ = await _run(server, ReplayProvider(cassette), "INC-2026-0101")  # different incident, same recording
    assert diverged.state.status is S.FAILED and "fingerprint mismatch" in diverged.state.error
    lenient = ReplayProvider(Cassette(provider=cassette.provider, entries=cassette.entries[:1]), strict=False)
    exhausted, _ = await _run(server, lenient, "INC-2026-0106")
    assert exhausted.state.status is S.FAILED and "cassette exhausted" in exhausted.state.error
    with pytest.raises(ReplayMismatch):
        ReplayProvider(Cassette(provider=cassette.provider, entries=[]))._next("turn", "sha256:x")


def test_redaction_never_stores_secrets():
    assert redact("key sk-abcdefghijklmnop123456 here") == "key [REDACTED] here"
    assert redact("Authorization: Bearer abc.def-ghi_jkl123456") == "Authorization: [REDACTED]"
    assert redact("api_key=supersecretvalue") == "api_key=[REDACTED]"
    assert redact("password: hunter2hunter2") == "password: [REDACTED]"
    assert redact("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345") == "[REDACTED]"
    assert redact("nothing sensitive: latency_ms=12") == "nothing sensitive: latency_ms=12"
    assert redact(None) is None


async def test_trace_persists_to_file_and_exports(server, tmp_path):
    path = tmp_path / "runs" / "trace.sqlite"
    store = TraceStore(path)
    result, _ = await _run(server, ScriptedDemoProvider(), "INC-2026-0104", trace=store, investigation_id="t-file")
    store.close()

    reopened = TraceStore(path)
    assert reopened.list_investigations()[0]["id"] == "t-file"
    bundle = reopened.load("t-file")
    assert bundle["investigation"]["status"] == "completed" and bundle["investigation"]["provider_mode"] == "scripted"
    assert bundle["investigation"]["uses_llm"] == 0 and bundle["investigation"]["incident"]["id"] == "INC-2026-0104"
    assert bundle["investigation"]["budget"]["max_steps"] == 8
    assert {e["evidence_id"] for e in bundle["evidence"]} == set(result.registry.ids())
    assert all(a["evidence_id"] for a in bundle["actions"] if a["kind"] in ("call_tool", "read_resource"))
    assert bundle["hypothesis_updates"] and bundle["validations"][0]["ok"] == 1
    assert bundle["report"]["incident_id"] == "INC-2026-0104"

    export = export_investigation(reopened, "t-file")
    assert export["schema_version"] == SCHEMA_VERSION and export["export_kind"] == "signalforge.investigation_trace"
    summary = export["inspection_summary"]
    assert summary["evidence_gathered"] == result.registry.ids()
    assert set(summary["evidence_cited_in_report"]) <= set(summary["evidence_gathered"])
    assert "EVD-000001" in summary["evidence_gathered_but_uncited"]  # the incident queue seed is never cited
    out = write_export(reopened, "t-file", tmp_path / "export" / "t-file.json")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["report"]["status"] == "root_cause_identified"
    assert reopened.load("nope") is None
    with pytest.raises(KeyError):
        export_investigation(reopened, "nope")


def test_schema_version_mismatch_is_refused(tmp_path):
    path = tmp_path / "old.sqlite"
    TraceStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="schema version"):
        TraceStore(path)


def test_trace_store_redacts_free_text_fields():
    store = TraceStore(":memory:")
    store.start_investigation(investigation_id="x", incident_id="INC", provider={"name": "p", "model": "m", "mode": "scripted", "uses_llm": False},
                              transport="in-memory", budget={})
    store.record_repair("x", 1, "please use token=abcdef123456 to retry")
    store.record_model_call("x", {"id": "mc1", "purpose": "deliberate", "response": {"text": "sk-abcdefghijklmnopqrstu"}})
    bundle = store.load("x")
    assert "[REDACTED]" in bundle["repairs"][0]["request_text"] and "abcdef123456" not in bundle["repairs"][0]["request_text"]
    assert "sk-abcdefghijklmnopqrstu" not in json.dumps(bundle["model_calls"][0]["response"])
