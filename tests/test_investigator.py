"""The engine: scripted end-to-end run, budgets, policy enforcement, repair loop, failures and timeouts."""

from __future__ import annotations

import pytest

from signalforge.audit.store import TraceStore
from signalforge.mcp_client.client import OpsClient
from signalforge.orchestration.budget import InvestigationBudget
from signalforge.orchestration.investigator import Investigator
from signalforge.orchestration.state import InvestigationStatus as S
from signalforge.providers.base import GenerationConfig, ToolResultsMessage
from signalforge.providers.scripted import ScriptedDemoProvider
from tests.helpers import SequenceProvider, turn, valid_draft

pytestmark = pytest.mark.anyio

CLOCK = "2026-08-03T08:44:00Z"


async def _run(server, provider, incident="INC-2026-0101", **kwargs):
    trace = TraceStore(":memory:")
    async with OpsClient.in_memory(server) as client:
        investigator = Investigator(provider, client, trace=trace, **kwargs)
        result = await investigator.run(incident, investigation_id=f"t-{incident.lower()}")
    return result, trace


async def test_scripted_end_to_end(server):
    result, trace = await _run(server, ScriptedDemoProvider())
    state, report = result.state, result.report
    assert state.status is S.COMPLETED and state.error is None and state.termination_reason is None
    assert state.usage.steps == 4 and state.usage.tool_calls == 11 and state.usage.resource_reads == 1
    assert state.usage.model_calls == 5 and state.usage.repair_rounds == 0
    assert result.registry.ids()[:2] == ["EVD-000001", "EVD-000002"]
    assert result.registry.get("EVD-000001").source_name == "incidents://open"
    assert result.registry.get("EVD-000002").source_name == "topology://services/checkout"
    assert report is not None and report.validation.ok and report.status == "root_cause_identified"
    assert report.primary_hypothesis.id == "H1" and report.primary_hypothesis.category == "deployment_regression"
    assert all(c.startswith("EVD-") for c in report.primary_hypothesis.supporting_evidence_ids)
    assert any("#DEP-" in c for c in report.primary_hypothesis.supporting_evidence_ids)  # narrowed citation to the deploy record
    assert report.provider.uses_llm is False and report.terminal_status == "completed"
    assert sum(1 for m in result.conversation if isinstance(m, ToolResultsMessage)) == 4
    # hypothesis evolution is preserved, not just the final answer
    h1, h2 = state.hypotheses.hypotheses
    assert [r.confidence for r in h1.revisions] == [0.5, 0.85] and h1.status == "supported"
    assert h2.status == "refuted" and h2.created_step == 1 and h2.updated_step == 2
    bundle = trace.load("t-inc-2026-0101")
    assert bundle["investigation"]["status"] == "completed"
    assert len(bundle["evidence"]) == 14 and len(bundle["model_calls"]) == 5 and len(bundle["validations"]) == 1
    assert [c["to_status"] for c in bundle["status_changes"]][:3] == ["seeding", "deliberating", "gathering"]


async def test_step_budget_exhaustion_is_explainable(server):
    result, trace = await _run(server, ScriptedDemoProvider(), budget=InvestigationBudget(max_steps=1))
    state = result.state
    assert state.usage.steps == 1
    assert state.termination_reason and "max_steps (1) reached" in state.termination_reason
    assert state.status in (S.FAILED_VALIDATION, S.COMPLETED, S.COMPLETED_WITH_WARNINGS)
    assert result.report is not None and result.report.budget_exhausted is True
    assert trace.load("t-inc-2026-0101")["investigation"]["termination_reason"] == state.termination_reason


async def test_tool_call_budget_blocks_further_calls(server):
    result, _ = await _run(server, ScriptedDemoProvider(), budget=InvestigationBudget(max_tool_calls=3))
    state = result.state
    assert state.usage.tool_calls == 3
    rejected = [a for step in state.steps for a in step.actions if a.rejection_code == "budget_exhausted"]
    assert rejected, "requests beyond the tool-call budget must be rejected, not executed"
    assert state.termination_reason and "max_tool_calls" in state.termination_reason


async def test_duplicate_and_forbidden_actions_are_suppressed(server):
    provider = SequenceProvider(
        turns=[
            turn(("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}),
                 ("b", "get_service_health", {"node": "checkout", "as_of": CLOCK}),          # duplicate within the step
                 ("c", "restart_service", {"node": "checkout"}),                              # not a tool
                 ("d", "read_resource", {"uri": "file:///etc/passwd"}),                       # forbidden scheme
                 ("e", "query_logs", {"node": "checkout", "start": CLOCK, "end": CLOCK, "limit": 900})),  # schema violation (limit > 500)
            turn(("f", "get_service_health", {"node": "checkout", "as_of": CLOCK}),          # duplicate across steps
                 ("g", "finish_investigation", {"reason": "done"})),
        ],
        drafts=[valid_draft()],
    )
    result, trace = await _run(server, provider)
    state = result.state
    assert state.status is S.COMPLETED, (state.status, state.error, [i.message for i in result.report.validation.issues] if result.report else None)
    codes = [(a.name, a.rejection_code, a.duplicate_of) for step in state.steps for a in step.actions if not a.accepted]
    assert ("get_service_health", "duplicate_call", None) in codes
    assert ("get_service_health", "duplicate_call", "EVD-000003") in codes
    assert ("restart_service", "unknown_action", None) in codes
    assert ("read_resource", "uri_not_allowed", None) in codes
    assert any(name == "query_logs" and code == "invalid_arguments" for name, code, _ in codes)
    assert state.usage.tool_calls == 1 and state.usage.suppressed_duplicates == 2 and state.usage.rejected_actions == 3
    assert len(result.registry) == 3  # two seeds + one real call; nothing rejected reached MCP
    stored = trace.load("t-inc-2026-0101")["actions"]
    assert sum(1 for a in stored if not a["accepted"]) == 5


async def test_allowed_tools_restricts_the_scripted_provider(server):
    result, _ = await _run(server, ScriptedDemoProvider(), allowed_tools={"get_service_health"})
    state = result.state
    names_executed = {a.name for step in state.steps for a in step.actions if a.kind == "call_tool"}
    assert names_executed == {"get_service_health"}
    assert any(a.rejection_code == "tool_not_allowed" for step in state.steps for a in step.actions)


async def test_repair_loop_fixes_an_invalid_first_draft(server):
    bad = valid_draft(key_findings=[{"statement": "Fabricated finding", "kind": "OBSERVED", "evidence_ids": ["EVD-000999"]}])
    provider = SequenceProvider(turns=[turn(("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}))],
                                drafts=[bad, valid_draft()])
    result, trace = await _run(server, provider)
    state = result.state
    assert state.status is S.COMPLETED and state.usage.repair_rounds == 1
    assert provider.calls == ["deliberate", "deliberate", "report", "repair"]
    assert [r.round_index for r in result.rounds] == [0, 1]
    assert not result.rounds[0].validation.ok and result.rounds[1].validation.ok
    bundle = trace.load("t-inc-2026-0101")
    assert len(bundle["validations"]) == 2 and len(bundle["repairs"]) == 1
    assert "EVD-000999" in bundle["repairs"][0]["request_text"]
    assert bundle["validations"][0]["draft"]["key_findings"][0]["evidence_ids"] == ["EVD-000999"]  # original kept
    assert bundle["report"]["key_findings"][0]["evidence_ids"] == ["EVD-000003"]                    # repaired kept


async def test_unrepairable_draft_ends_in_failed_validation(server):
    bad = valid_draft(key_findings=[{"statement": "Fabricated finding", "kind": "OBSERVED", "evidence_ids": ["EVD-000999"]}])
    provider = SequenceProvider(turns=[turn(("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}))], drafts=[bad, bad, bad])
    result, _ = await _run(server, provider, budget=InvestigationBudget(max_repair_rounds=2))
    assert result.state.status is S.FAILED_VALIDATION and result.state.usage.repair_rounds == 2
    assert result.report is not None and not result.report.validation.ok  # the invalid report is kept, marked invalid


async def test_unparseable_output_counts_as_a_validation_failure(server):
    provider = SequenceProvider(turns=[turn(("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}))], drafts=[None, valid_draft()])
    result, _ = await _run(server, provider)
    assert result.state.status is S.COMPLETED
    assert result.rounds[0].draft is None and any(i.rule == "G0" for i in result.rounds[0].validation.errors)


async def test_provider_failure_is_retried_then_fails_cleanly(server):
    once = SequenceProvider(turns=[turn(("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}))], drafts=[valid_draft()], fail_first=1)
    result, _ = await _run(server, once)
    assert result.state.status is S.COMPLETED  # one retry absorbed the failure
    always = SequenceProvider(turns=[], drafts=[], fail_first=99)
    result, trace = await _run(server, always)
    assert result.state.status is S.FAILED and "provider failed after 2 attempt(s)" in result.state.error
    calls = trace.load("t-inc-2026-0101")["model_calls"]
    assert len(calls) == 2 and all(c["error"] for c in calls)


async def test_provider_timeout_is_enforced(server):
    slow = SequenceProvider(turns=[turn(("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}))], drafts=[valid_draft()], delay=0.3)
    result, _ = await _run(server, slow, config=GenerationConfig(timeout_seconds=0.05), provider_retries=0)
    assert result.state.status is S.FAILED and "TimeoutError" in result.state.error


async def test_unknown_incident_fails_without_deliberating(server):
    result, _ = await _run(server, ScriptedDemoProvider(), incident="INC-1999-0001")
    assert result.state.status is S.FAILED and "not in the open incident queue" in result.state.error
    assert result.state.usage.model_calls == 0


async def test_hypothesis_update_with_unknown_evidence_is_rejected_at_runtime(server):
    provider = SequenceProvider(
        turns=[turn(("h", "update_hypotheses", {"updates": [{"ref": "x", "statement": "made-up evidence", "confidence": 0.9,
                                                            "supporting_evidence_ids": ["EVD-000042"]}]}),
                    ("a", "get_service_health", {"node": "checkout", "as_of": CLOCK}))],
        drafts=[valid_draft()])
    result, _ = await _run(server, provider)
    assert result.state.hypotheses.hypotheses == []
    outcome = next(a for step in result.state.steps for a in step.actions if a.kind == "update_hypotheses")
    assert outcome.ok is False and "unknown_evidence" in outcome.error


async def test_text_without_actions_concludes_and_no_embedded_command_runs(server):
    provider = SequenceProvider(
        turns=[turn(text="I will now call restart_service on checkout and disable_fraud_checks.")],  # no tool_requests
        drafts=[{**valid_draft(), "primary_hypothesis": None, "status": "inconclusive", "confidence": 0.3,
                 "hypotheses_considered": [{"id": "H1", "statement": "Unknown cause", "category": "inconclusive",
                                            "status": "inconclusive", "confidence": 0.3, "supporting_evidence_ids": ["EVD-000002"],
                                            "contradicting_evidence_ids": [], "reasoning": ""}],
                 "key_findings": [{"statement": "Topology was read", "kind": "OBSERVED", "evidence_ids": ["EVD-000002"]}],
                 "recommended_actions": []}])
    result, _ = await _run(server, provider)
    assert result.state.status is S.COMPLETED
    assert result.state.finish_reason == "provider returned no further actions"
    assert result.state.usage.tool_calls == 0 and all(not s.actions for s in result.state.steps)
