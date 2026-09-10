"""Bounded investigation orchestration: typed actions, explicit state, budgets, policy, and the engine.

The orchestrator owns state, budgets, the action catalogue, MCP calls, the evidence
registry, grounding validation, repair rounds and trace persistence. Providers only
turn a conversation into one turn.
"""

from signalforge.orchestration.actions import (
    Action,
    ActionRejection,
    CallTool,
    FinishInvestigation,
    HypothesisUpdate,
    ReadResource,
    UpdateHypotheses,
    local_action_specs,
    parse_action,
)
from signalforge.orchestration.budget import BudgetUsage, InvestigationBudget
from signalforge.orchestration.hypotheses import Hypothesis, HypothesisBoard
from signalforge.orchestration.investigator import InvestigationResult, Investigator
from signalforge.orchestration.policy import ActionPolicy, PolicyDecision
from signalforge.orchestration.state import (
    TERMINAL_STATUSES,
    ActionOutcome,
    IllegalTransition,
    InvestigationState,
    InvestigationStatus,
    StepRecord,
    transition,
)

__all__ = [
    "TERMINAL_STATUSES",
    "Action",
    "ActionOutcome",
    "ActionPolicy",
    "ActionRejection",
    "BudgetUsage",
    "CallTool",
    "FinishInvestigation",
    "Hypothesis",
    "HypothesisBoard",
    "HypothesisUpdate",
    "IllegalTransition",
    "InvestigationBudget",
    "InvestigationResult",
    "InvestigationState",
    "InvestigationStatus",
    "Investigator",
    "PolicyDecision",
    "ReadResource",
    "StepRecord",
    "UpdateHypotheses",
    "local_action_specs",
    "parse_action",
    "transition",
]
