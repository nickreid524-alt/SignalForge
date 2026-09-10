"""Deterministic evaluation harness: run scenarios through the real pipeline and score the results.

This package is on the *evaluation* side of the ground-truth firewall: it may
read scenario specs and the fault manifest. Nothing in orchestration,
providers, MCP or retrieval imports it.
"""

from signalforge.evals.metrics import (
    EvaluationSummary,
    ScenarioEvaluation,
    evaluate_scenario,
    summarize,
)
from signalforge.evals.report import comparable, render_markdown, render_table
from signalforge.evals.runner import run_evaluation, run_evaluation_async

__all__ = [
    "EvaluationSummary",
    "ScenarioEvaluation",
    "comparable",
    "evaluate_scenario",
    "render_markdown",
    "render_table",
    "run_evaluation",
    "run_evaluation_async",
    "summarize",
]
