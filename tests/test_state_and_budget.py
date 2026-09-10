"""State machine transitions and budget accounting are explicit and testable without a provider or server."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from signalforge.orchestration.budget import (
    BudgetUsage,
    InvestigationBudget,
    deliberation_exhausted,
)
from signalforge.orchestration.state import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    IllegalTransition,
    InvestigationState,
    transition,
)
from signalforge.orchestration.state import (
    InvestigationStatus as S,
)


def _state() -> InvestigationState:
    return InvestigationState(investigation_id="inv-test", incident_id="INC-2026-0101")


def test_happy_path_transitions_are_legal_and_recorded():
    state = _state()
    for status in (S.SEEDING, S.DELIBERATING, S.GATHERING, S.DELIBERATING, S.GATHERING, S.CONCLUDING, S.VALIDATING,
                   S.REPAIRING, S.VALIDATING, S.COMPLETED):
        transition(state, status, note=f"-> {status.value}")
    assert state.status is S.COMPLETED and state.is_terminal
    assert [c.to_status for c in state.history][-3:] == [S.REPAIRING, S.VALIDATING, S.COMPLETED]
    assert all(c.note for c in state.history)


@pytest.mark.parametrize(("frm", "to"), [
    (S.CREATED, S.DELIBERATING), (S.CREATED, S.COMPLETED), (S.SEEDING, S.GATHERING), (S.DELIBERATING, S.VALIDATING),
    (S.GATHERING, S.VALIDATING), (S.CONCLUDING, S.COMPLETED), (S.VALIDATING, S.DELIBERATING), (S.REPAIRING, S.COMPLETED),
    (S.COMPLETED, S.DELIBERATING), (S.FAILED, S.SEEDING), (S.FAILED_VALIDATION, S.REPAIRING),
])
def test_illegal_transitions_raise(frm, to):
    state = _state()
    state.status = frm
    with pytest.raises(IllegalTransition):
        transition(state, to)
    assert state.status is frm and state.history == []


def test_terminal_states_have_no_exits_and_every_active_state_can_fail():
    for status in TERMINAL_STATUSES:
        assert TRANSITIONS[status] == frozenset()
    for status, targets in TRANSITIONS.items():
        if status not in TERMINAL_STATUSES:
            assert S.FAILED in targets, status
    assert set(TRANSITIONS) == set(S)


def test_budget_exhaustion_reasons():
    budget = InvestigationBudget(max_steps=3, max_tool_calls=5, max_model_calls=6, max_repair_rounds=1, max_wall_clock_seconds=10)
    assert deliberation_exhausted(budget, BudgetUsage()) == []
    assert "max_steps" in deliberation_exhausted(budget, BudgetUsage(steps=3))[0]
    assert "max_tool_calls" in deliberation_exhausted(budget, BudgetUsage(tool_calls=5))[0]
    # 6 model calls, 2 reserved for report + repair -> deliberation must stop at 4
    assert deliberation_exhausted(budget, BudgetUsage(model_calls=3)) == []
    assert "reserved for reporting" in deliberation_exhausted(budget, BudgetUsage(model_calls=4))[0]
    assert "max_wall_clock_seconds" in deliberation_exhausted(budget, BudgetUsage(elapsed_seconds=10.5))[0]
    reasons = deliberation_exhausted(budget, BudgetUsage(steps=3, tool_calls=5, model_calls=4, elapsed_seconds=99))
    assert len(reasons) == 4


def test_budget_remaining_and_validation():
    budget = InvestigationBudget(max_steps=8, max_tool_calls=24, max_model_calls=14)
    usage = BudgetUsage(steps=2, tool_calls=5, resource_reads=1, model_calls=3)
    remaining = usage.remaining(budget)
    assert remaining == {"steps": 6, "tool_calls": 19, "resource_reads": 11, "model_calls": 11, "repair_rounds": 1}
    assert budget.reserved_model_calls() == 2
    with pytest.raises(ValidationError):
        InvestigationBudget(max_steps=0)
    with pytest.raises(ValidationError):
        InvestigationBudget(max_model_calls=1)
    with pytest.raises(ValidationError):
        InvestigationBudget(max_wall_clock_seconds=0)
    with pytest.raises(ValidationError):
        InvestigationBudget(unknown_dimension=3)  # type: ignore[call-arg]
