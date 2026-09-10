"""Presentation rendering of the typed report (plain text / Markdown). Never the source of truth."""

from __future__ import annotations

from signalforge.reports.schema import InvestigationReport


def _cite(ids: list[str]) -> str:
    return f" [{', '.join(ids)}]" if ids else ""


def render_markdown(report: InvestigationReport) -> str:
    lines = [
        f"# Investigation {report.investigation_id} - {report.incident_id}",
        "",
        f"- provider: {report.provider.name} ({report.provider.mode}; uses_llm={report.provider.uses_llm})",
        f"- terminal status: {report.terminal_status}",
        f"- report status: {report.status}  confidence: {report.confidence:.2f}",
        f"- steps {report.steps_used} | tool calls {report.tool_calls_used} | resource reads {report.resource_reads_used} "
        f"| model calls {report.model_calls_used} | repairs {report.repair_rounds} | duration {report.investigation_duration_seconds:.1f}s",
        "- tokens: " + (f"{report.token_usage.input_tokens} in / {report.token_usage.output_tokens} out "
                        f"(cached {report.token_usage.cached_input_tokens}, reasoning {report.token_usage.reasoning_output_tokens})"
                        if report.token_usage.reported else "not reported (provider uses no LLM)"),
        f"- validation: {'ok' if report.validation.ok else 'FAILED'} "
        f"({len(report.validation.errors)} error(s), {len(report.validation.warnings)} warning(s))",
        "",
        "## Summary",
        report.summary,
        "",
    ]
    if report.primary_hypothesis:
        p = report.primary_hypothesis
        lines += ["## Primary hypothesis",
                  f"**{p.id}** [{p.category}] {p.statement}{_cite(p.supporting_evidence_ids)}",
                  f"status: {p.status}  confidence: {p.confidence:.2f}"]
        if p.reasoning:
            lines.append(f"reasoning: {p.reasoning}")
        lines.append("")
    lines.append("## Hypotheses considered")
    for h in report.hypotheses_considered:
        lines.append(f"- {h.id} [{h.category}, {h.status}, {h.confidence:.2f}] {h.statement}"
                     f"{_cite(h.supporting_evidence_ids)}"
                     + (f" contradicted by {', '.join(h.contradicting_evidence_ids)}" if h.contradicting_evidence_ids else ""))
    lines += ["", "## Key findings"]
    lines += [f"- {c.kind}: {c.statement}{_cite(c.evidence_ids)}" for c in report.key_findings]
    if report.contradicting_evidence:
        lines += ["", "## Contradicting evidence"]
        lines += [f"- {c.kind}: {c.statement}{_cite(c.evidence_ids)}" for c in report.contradicting_evidence]
    if report.recommended_actions:
        lines += ["", "## Recommended actions"]
        lines += [f"- {a.priority} {a.kind}: {a.action} - {a.rationale}{_cite(a.evidence_ids)}" for a in report.recommended_actions]
    if report.unknowns:
        lines += ["", "## Unknowns"]
        lines += [f"- {c.statement}" for c in report.unknowns]
    if report.limitations:
        lines += ["", "## Limitations"]
        lines += [f"- {text}" for text in report.limitations]
    lines += ["", "## Evidence index"]
    lines += [f"- {e.evidence_id} {e.source_kind} {e.source_name} ({e.result_kind}, {e.record_count} records)"
              f"{' cited' if e.cited else ''}{'' if e.ok else ' FAILED'}" for e in report.evidence_index]
    if report.validation.issues:
        lines += ["", "## Validation issues"]
        lines += [f"- {i.severity.upper()} [{i.rule}] {i.path}: {i.message}" for i in report.validation.issues]
    return "\n".join(lines) + "\n"
