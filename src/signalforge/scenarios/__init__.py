"""Scenario ground truth: root causes, decisive evidence, red herrings, unacceptable conclusions.

Nothing in ``signalforge.world``, ``signalforge.mcp_server`` or
``signalforge.retrieval`` imports this package. The MCP server therefore cannot
observe any of it; only scenario and evaluation code can.
"""

from signalforge.scenarios.catalogue import SCENARIOS, scenario_by_id, scenario_for_incident
from signalforge.scenarios.ground_truth import GroundTruth, PredicateResult
from signalforge.scenarios.models import (
    EvidencePredicate,
    FailureCategory,
    MisleadingEvidence,
    ScenarioSpec,
    UnacceptableConclusion,
)

__all__ = [
    "SCENARIOS",
    "EvidencePredicate",
    "FailureCategory",
    "GroundTruth",
    "MisleadingEvidence",
    "PredicateResult",
    "ScenarioSpec",
    "UnacceptableConclusion",
    "scenario_by_id",
    "scenario_for_incident",
]
