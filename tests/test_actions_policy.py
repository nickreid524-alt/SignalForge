"""Typed actions, argument validation against MCP schemas, allowlists, duplicate suppression and limits."""

from __future__ import annotations

import pytest

from signalforge.orchestration.actions import (
    ActionRejection,
    CallTool,
    FinishInvestigation,
    ReadResource,
    UpdateHypotheses,
    local_action_specs,
    parse_action,
)
from signalforge.orchestration.policy import ActionPolicy, call_key
from signalforge.providers.base import ToolRequest, ToolSpec

HEALTH = ToolSpec(name="get_service_health", description="h", input_schema={
    "type": "object", "properties": {"node": {"type": "string", "pattern": "^[a-z][a-z0-9-]*$"},
                                     "as_of": {"type": "string", "format": "date-time"}},
    "required": ["node", "as_of"], "title": "Args"})
LOGS = ToolSpec(name="query_logs", description="l", input_schema={
    "type": "object", "properties": {"node": {"type": "string"}, "limit": {"type": "integer", "maximum": 500, "minimum": 1}},
    "required": ["node"]})
TOOLS = {t.name: t for t in [HEALTH, LOGS, *local_action_specs()]}


def test_local_action_specs_are_published_like_tools():
    names = {s.name for s in local_action_specs()}
    assert names == {"read_resource", "update_hypotheses", "finish_investigation"}
    assert all(s.local and s.input_schema.get("type") == "object" and s.description for s in local_action_specs())


def test_parse_each_action_kind():
    assert isinstance(parse_action(ToolRequest(id="1", name="get_service_health", arguments={"node": "checkout", "as_of": "2026-08-03T08:44:00Z"}), TOOLS), CallTool)
    assert isinstance(parse_action(ToolRequest(id="2", name="read_resource", arguments={"uri": "runbook://RB-016"}), TOOLS), ReadResource)
    upd = parse_action(ToolRequest(id="3", name="update_hypotheses", arguments={"updates": [{"statement": "something broke", "confidence": 0.4}]}), TOOLS)
    assert isinstance(upd, UpdateHypotheses) and upd.updates[0].status == "proposed"
    fin = parse_action(ToolRequest(id="4", name="finish_investigation", arguments={}), TOOLS)
    assert isinstance(fin, FinishInvestigation) and fin.reason == ""


@pytest.mark.parametrize(("request_", "code", "fragment"), [
    (ToolRequest(id="a", name="restart_service", arguments={"node": "checkout"}), "unknown_action", "not an available tool"),
    (ToolRequest(id="b", name="get_service_health", arguments={"node": "checkout"}), "invalid_arguments", "as_of"),
    (ToolRequest(id="c", name="get_service_health", arguments={"node": "Checkout", "as_of": "x"}), "invalid_arguments", "does not match"),
    (ToolRequest(id="d", name="query_logs", arguments={"node": "checkout", "limit": 900}), "invalid_arguments", "maximum"),
    (ToolRequest(id="e", name="query_logs", arguments={"node": "checkout", "limit": "ten"}), "invalid_arguments", "integer"),
    (ToolRequest(id="f", name="update_hypotheses", arguments={"updates": []}), "invalid_arguments", "update"),
    (ToolRequest(id="g", name="update_hypotheses", arguments={"updates": [{"statement": "x", "confidence": 1.7}]}), "invalid_arguments", "confidence"),
    (ToolRequest(id="h", name="read_resource", arguments={}), "invalid_arguments", "uri"),
    (ToolRequest(id="i", name="finish_investigation", arguments={"reason": "x" * 600}), "invalid_arguments", "reason"),
])
def test_invalid_requests_are_rejected_not_executed(request_, code, fragment):
    result = parse_action(request_, TOOLS)
    assert isinstance(result, ActionRejection)
    assert result.code == code
    assert fragment.lower() in result.reason.lower(), result.reason


def test_natural_language_is_never_an_action():
    result = parse_action(ToolRequest(id="x", name="please restart checkout now", arguments={}), TOOLS)
    assert isinstance(result, ActionRejection) and result.code == "unknown_action"


def _policy(**kwargs) -> ActionPolicy:
    return ActionPolicy(TOOLS, **kwargs)


def _health(request_id: str, node: str = "checkout") -> ToolRequest:
    return ToolRequest(id=request_id, name="get_service_health", arguments={"node": node, "as_of": "2026-08-03T08:44:00Z"})


def test_policy_allowlist_and_unknown_allowlist_entries():
    policy = _policy(allowed_tools={"get_service_health"})
    decisions = policy.review([_health("1"), ToolRequest(id="2", name="query_logs", arguments={"node": "checkout"})],
                              seen_calls={}, remaining_tool_calls=10, remaining_resource_reads=10)
    assert decisions[0].accepted and decisions[1].rejection.code == "tool_not_allowed"
    with pytest.raises(ValueError, match="does not expose"):
        _policy(allowed_tools={"teleport"})


def test_policy_suppresses_duplicates_across_and_within_steps():
    policy = _policy()
    seen = {call_key("get_service_health", {"node": "checkout", "as_of": "2026-08-03T08:44:00Z"}): "EVD-000004"}
    decisions = policy.review([_health("1"), _health("2", node="orders"), _health("3", node="orders")],
                              seen_calls=seen, remaining_tool_calls=10, remaining_resource_reads=10)
    assert decisions[0].rejection.code == "duplicate_call" and decisions[0].duplicate_of == "EVD-000004"
    assert decisions[1].accepted
    assert decisions[2].rejection.code == "duplicate_call" and decisions[2].duplicate_of is None
    # argument order does not defeat de-duplication
    reordered = ToolRequest(id="4", name="get_service_health", arguments={"as_of": "2026-08-03T08:44:00Z", "node": "checkout"})
    assert policy.review([reordered], seen_calls=seen, remaining_tool_calls=10, remaining_resource_reads=10)[0].rejection.code == "duplicate_call"


def test_policy_enforces_per_step_and_budget_limits():
    policy = _policy(max_actions_per_step=2)
    requests = [_health(str(i), node=f"svc{i}") for i in range(4)]
    decisions = policy.review(requests, seen_calls={}, remaining_tool_calls=1, remaining_resource_reads=0)
    assert decisions[0].accepted
    assert decisions[1].rejection.code == "budget_exhausted"
    assert decisions[2].rejection.code == "limit_exceeded" and decisions[3].rejection.code == "limit_exceeded"
    assert len(decisions) == len(requests)  # every request gets a decision


def test_policy_validates_resource_uris():
    policy = _policy()
    reads = [ToolRequest(id="1", name="read_resource", arguments={"uri": "runbook://RB-016"}),
             ToolRequest(id="2", name="read_resource", arguments={"uri": "file:///etc/passwd"}),
             ToolRequest(id="3", name="read_resource", arguments={"uri": "runbook://RB-016"}),
             ToolRequest(id="4", name="read_resource", arguments={"uri": "no-scheme"})]
    decisions = policy.review(reads, seen_calls={}, remaining_tool_calls=5, remaining_resource_reads=5)
    assert decisions[0].accepted
    assert decisions[1].rejection.code == "uri_not_allowed"
    assert decisions[2].rejection.code == "duplicate_call"
    assert decisions[3].rejection.code == "uri_not_allowed"
    starved = policy.review(reads[:1], seen_calls={}, remaining_tool_calls=5, remaining_resource_reads=0)
    assert starved[0].rejection.code == "budget_exhausted"
