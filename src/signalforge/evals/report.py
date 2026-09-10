"""Rendering and comparison helpers for evaluation summaries."""

from __future__ import annotations

from typing import Any

from signalforge.evals.metrics import EvaluationSummary

# Fields that legitimately vary between runs and are excluded from golden comparisons.
VOLATILE_FIELDS = {"duration_seconds", "investigation_id"}


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_table(summary: EvaluationSummary) -> str:
    header = (f"{'scenario':<8} {'status':<24} {'predicted':<24} {'expected':<24} {'conf':>5} {'recall':>6} "
              f"{'cited':>6} {'cit.ok':>6} {'unsup':>5} {'herring':>7} {'tools':>5} {'dup':>3} {'rep':>3} {'pass':>5}")
    lines = [header, "-" * len(header)]
    for e in summary.scenarios:
        lines.append(
            f"{e.scenario_id:<8} {e.terminal_status:<24} {(e.predicted_category or '-'):<24} {e.expected_category:<24} "
            f"{_fmt(e.confidence):>5} {_fmt(e.decisive_evidence_recall):>6} {_fmt(e.decisive_citation_recall):>6} "
            f"{_fmt(e.citation_validity):>6} {_fmt(e.unsupported_claim_rate):>5} {e.red_herring_adopted!s:>7} "
            f"{e.tool_calls:>5} {e.suppressed_duplicates:>3} {e.repair_rounds:>3} {'PASS' if e.passed else 'FAIL':>5}"
        )
    lines.append("")
    lines.append(f"provider: {summary.provider} ({summary.provider_mode})")
    for key, value in summary.aggregates.items():
        lines.append(f"  {key:<32} {value:g}")
    failing = [e for e in summary.scenarios if not e.passed]
    if failing:
        lines.append("")
        lines.append("failures:")
        for e in failing:
            for reason in e.failures:
                lines.append(f"  {e.scenario_id}: {reason}")
    return "\n".join(lines)


def render_markdown(summary: EvaluationSummary) -> str:
    lines = [
        "# Evaluation results",
        "",
        f"Provider: **{summary.provider}** (mode: {summary.provider_mode}). Generated {summary.generated_at.isoformat()}.",
        "",
        "| Scenario | Status | Predicted cause | Expected cause | Confidence | Evidence recall | Cited recall | "
        "Citation validity | Unsupported claims | Red herring | Tool calls | Repairs | Pass |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in summary.scenarios:
        lines.append(
            f"| {e.scenario_id} | {e.terminal_status} | {e.predicted_category or '-'} | {e.expected_category} | {_fmt(e.confidence)} | "
            f"{_fmt(e.decisive_evidence_recall)} | {_fmt(e.decisive_citation_recall)} | {_fmt(e.citation_validity)} | "
            f"{_fmt(e.unsupported_claim_rate)} | {'yes' if e.red_herring_adopted else 'no'} | {e.tool_calls} | {e.repair_rounds} | "
            f"{'PASS' if e.passed else 'FAIL'} |"
        )
    lines += ["", "## Aggregates", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v:g} |" for k, v in summary.aggregates.items()]
    failing = [e for e in summary.scenarios if not e.passed]
    if failing:
        lines += ["", "## Failures", ""]
        lines += [f"- {e.scenario_id}: {'; '.join(e.failures)}" for e in failing]
    return "\n".join(lines) + "\n"


def comparable(summary: EvaluationSummary) -> dict[str, Any]:
    """Deterministic projection of a summary for golden-file comparison."""
    scenarios = []
    for e in summary.scenarios:
        data = e.model_dump(mode="json")
        for key in VOLATILE_FIELDS:
            data.pop(key, None)
        scenarios.append(data)
    return {"provider": summary.provider, "provider_mode": summary.provider_mode, "scenarios": scenarios,
            "aggregates": summary.aggregates}
