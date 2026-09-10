"""investigate / trace / eval commands."""

from __future__ import annotations

import json

from signalforge import cli


def test_investigate_scripted_and_trace(tmp_path, capsys):
    db = tmp_path / "trace.sqlite"
    export = tmp_path / "trace.json"
    code = cli.main(["investigate", "INC-2026-0106", "--trace-db", str(db), "--json", str(export), "--markdown"])
    out = capsys.readouterr().out
    assert code == 0
    assert "SCRIPTED DEMONSTRATION MODE" in out and "No LLM API is being used" in out
    assert "MCP: connected via in-memory" in out
    for section in ("INCIDENT  INC-2026-0106", "STEPS", "EVIDENCE", "HYPOTHESES (evolution)", "CONCLUSION",
                    "VALIDATION  ok", "TRACE ID", "# Investigation"):
        assert section in out, section
    assert "status: root_cause_identified" in out and "confidence: 0.90" in out
    assert "EVD-000001" in out and "H1 [supported" in out
    trace_id = next(line.split()[2] for line in out.splitlines() if line.startswith("TRACE ID"))
    exported = json.loads(export.read_text(encoding="utf-8"))
    assert exported["investigation"]["id"] == trace_id and exported["report"]["incident_id"] == "INC-2026-0106"

    assert cli.main(["trace", trace_id, "--trace-db", str(db)]) == 0
    trace_out = capsys.readouterr().out
    for section in ("STATUS CHANGES", "MODEL CALLS", "ACTIONS", "EVIDENCE INSPECTED", "HYPOTHESIS UPDATES",
                    "VALIDATION ROUNDS", "INSPECTED BEFORE CONCLUDING"):
        assert section in trace_out, section
    assert cli.main(["trace", "--list", "--trace-db", str(db)]) == 0
    assert trace_id in capsys.readouterr().out
    assert cli.main(["trace", "missing-id", "--trace-db", str(db)]) == 2


def test_investigate_rejects_unavailable_providers(tmp_path, capsys):
    # a live provider without --yes is refused before any client is built, whatever the environment holds
    assert cli.main(["investigate", "INC-2026-0101", "--provider", "anthropic", "--trace-db", str(tmp_path / "t.sqlite")]) == 2
    out = capsys.readouterr().out
    assert "LIVE API CONFIRMATION REQUIRED" in out and "Nothing was called" in out
    assert cli.main(["investigate", "INC-2026-0101", "--provider", "replay", "--trace-db", str(tmp_path / "t.sqlite")]) == 2
    assert "cassette" in capsys.readouterr().out


def test_investigate_budget_flags(tmp_path, capsys):
    code = cli.main(["investigate", "INC-2026-0101", "--trace-db", str(tmp_path / "t.sqlite"), "--max-steps", "1"])
    out = capsys.readouterr().out
    assert "BUDGET    exhausted: max_steps (1) reached" in out
    assert code in (0, 1)


def test_eval_single_scenario(tmp_path, capsys):
    out_json = tmp_path / "eval.json"
    md = tmp_path / "eval.md"
    code = cli.main(["eval", "--scenario", "SCN-06", "--json", str(out_json), "--markdown", str(md)])
    out = capsys.readouterr().out
    assert code == 0 and "SCRIPTED DEMONSTRATION MODE" in out
    assert "SCN-06" in out and "PASS" in out and "pass_rate" in out
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["scenarios"][0]["scenario_id"] == "SCN-06" and data["aggregates"]["passed"] == 1.0
    assert md.read_text(encoding="utf-8").startswith("# Evaluation results")
