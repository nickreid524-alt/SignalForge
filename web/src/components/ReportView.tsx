/**
 * The conclusion.
 *
 * The design goal is that a reviewer can answer "why did SignalForge say this?" without leaving the
 * page: every claim is tagged OBSERVED, INFERRED or UNKNOWN, and every citation is a button that
 * opens the evidence it points at. Narrowed citations like EVD-000004#DEP-0038 are shown in full and
 * open the parent evidence item.
 */

import { Badge, Confidence, Panel } from "@/components/primitives";
import { EvidenceLink, StatusPill } from "@/components/Timeline";
import type { Claim, InvestigationReport } from "@/types/api";

const STATUS_LABEL: Record<string, string> = {
  root_cause_identified: "Root cause identified",
  probable_cause: "Probable cause",
  inconclusive: "Inconclusive",
};

const KIND_TONE: Record<string, { bg: string; fg: string; border: string }> = {
  OBSERVED: { bg: "var(--ok-bg)", fg: "var(--ok)", border: "var(--ok-border)" },
  INFERRED: { bg: "var(--accent-soft)", fg: "var(--accent-strong)", border: "var(--accent-border)" },
  UNKNOWN: { bg: "var(--warn-bg)", fg: "var(--warn)", border: "var(--warn-border)" },
};

export function ReportView({ report, onSelectEvidence }: {
  report: InvestigationReport;
  onSelectEvidence?: (id: string) => void;
}) {
  const validationErrors = report.validation.issues.filter((issue) => issue.severity === "error");
  const validationWarnings = report.validation.issues.filter((issue) => issue.severity === "warning");
  const inconclusive = report.status === "inconclusive";

  return (
    <div className="stack">
      <Panel
        title="Conclusion"
        actions={
          <div className="row">
            <Badge tone={validationErrors.length === 0 ? "ok" : "danger"}>
              {validationErrors.length === 0 ? "GROUNDING VALIDATED" : `${validationErrors.length} GROUNDING ERRORS`}
            </Badge>
            {report.repair_rounds > 0 && <Badge tone="warn">{report.repair_rounds} repair round(s)</Badge>}
          </div>
        }
      >
        <div className="stack">
          <div className="row row--wrap" style={{ gap: "var(--space-3)" }}>
            <Badge tone={inconclusive ? "warn" : "accent"}>{STATUS_LABEL[report.status] ?? report.status}</Badge>
            <span className="row" style={{ gap: 8 }}>
              <span className="small muted">confidence</span>
              <Confidence value={report.confidence} tone={inconclusive ? "var(--warn)" : "var(--ok)"} />
            </span>
          </div>

          {report.primary_hypothesis ? (
            <div
              style={{
                padding: "var(--space-4)", background: "var(--surface-sunken)",
                border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
              }}
            >
              <div className="row" style={{ gap: 8, marginBottom: 6 }}>
                <span className="xs muted" style={{ fontWeight: 700, letterSpacing: "0.06em" }}>PRIMARY HYPOTHESIS</span>
                <span className="mono small" style={{ fontWeight: 700 }}>{report.primary_hypothesis.id}</span>
                <StatusPill status={report.primary_hypothesis.status} />
                <Badge>{report.primary_hypothesis.category.replace(/_/g, " ")}</Badge>
              </div>
              <p style={{ margin: "0 0 8px", fontSize: "var(--text-lg)", lineHeight: 1.5, fontWeight: 500 }}>
                {report.primary_hypothesis.statement}
              </p>
              {report.primary_hypothesis.reasoning && (
                <p className="small muted" style={{ margin: "0 0 8px" }}>{report.primary_hypothesis.reasoning}</p>
              )}
              <Citations ids={report.primary_hypothesis.supporting_evidence_ids} onSelect={onSelectEvidence} label="cites" />
            </div>
          ) : (
            <div className="banner banner--warn">
              No primary hypothesis was established. The evidence gathered did not support one.
            </div>
          )}

          <div>
            <h3 className="panel__title" style={{ marginBottom: 6 }}>Summary</h3>
            <p style={{ margin: 0, lineHeight: 1.65 }}>{report.summary}</p>
          </div>
        </div>
      </Panel>

      <ClaimSection title="Observed" kind="OBSERVED" claims={report.key_findings.filter((c) => c.kind === "OBSERVED")}
        onSelectEvidence={onSelectEvidence} description="Read directly from tool results." />
      <ClaimSection title="Inferred" kind="INFERRED" claims={report.key_findings.filter((c) => c.kind === "INFERRED")}
        onSelectEvidence={onSelectEvidence} description="Reasoning over the observations, each tied to evidence." />
      <ClaimSection title="Unknown" kind="UNKNOWN" claims={report.unknowns} onSelectEvidence={onSelectEvidence}
        description="Explicitly not established by this investigation." />

      {report.contradicting_evidence.length > 0 && (
        <ClaimSection title="Contradicting evidence" kind="OBSERVED" claims={report.contradicting_evidence}
          onSelectEvidence={onSelectEvidence} description="Evidence that argues against the conclusion, kept rather than dropped." />
      )}

      {report.recommended_actions.length > 0 && (
        <Panel title="Recommended actions" flush>
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {report.recommended_actions.map((action, index) => (
              <li key={index} style={{ padding: "var(--space-3) var(--space-4)", borderBottom: "1px solid var(--border-hairline)" }}>
                <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                  <Badge tone={action.priority === "P1" ? "danger" : action.priority === "P2" ? "warn" : "neutral"}>
                    {action.priority}
                  </Badge>
                  <Badge>{action.kind.replace(/_/g, " ")}</Badge>
                </div>
                <p style={{ margin: "0 0 3px", fontWeight: 500 }}>{action.action}</p>
                <p className="small muted" style={{ margin: "0 0 4px" }}>{action.rationale}</p>
                <Citations ids={action.evidence_ids} onSelect={onSelectEvidence} />
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div className="grid-2" style={{ alignItems: "start" }}>
        {report.limitations.length > 0 && (
          <Panel title="Limitations">
            <ul style={{ margin: 0, paddingLeft: "var(--space-4)" }} className="small">
              {report.limitations.map((limitation, index) => (
                <li key={index} style={{ marginBottom: 4 }}>{limitation}</li>
              ))}
            </ul>
          </Panel>
        )}

        <Panel title="Validation">
          <div className="stack stack--tight">
            <div className="row">
              <Badge tone={validationErrors.length === 0 ? "ok" : "danger"}>
                {validationErrors.length} errors
              </Badge>
              <Badge tone={validationWarnings.length === 0 ? "neutral" : "warn"}>
                {validationWarnings.length} warnings
              </Badge>
              <span className="small muted">{report.repair_rounds} repair round(s)</span>
            </div>
            {report.validation.issues.length === 0 ? (
              <p className="small muted" style={{ margin: 0 }}>
                Every claim cites evidence that exists in this investigation, and every citation resolves.
              </p>
            ) : (
              <ul style={{ margin: 0, paddingLeft: "var(--space-4)" }} className="small">
                {report.validation.issues.map((issue, index) => (
                  <li key={index} style={{ marginBottom: 3 }}>
                    <span className="mono xs">{issue.rule}</span>{" "}
                    <span className={issue.severity === "error" ? "" : "muted"}>{issue.message}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="xs dim" style={{ margin: "6px 0 0" }}>
              {report.tool_calls_used} tool calls · {report.resource_reads_used} resource reads ·{" "}
              {report.model_calls_used} provider calls · {report.rejected_actions} refused actions ·{" "}
              {report.token_usage.reported
                ? `${report.token_usage.input_tokens} in / ${report.token_usage.output_tokens} out tokens`
                : "tokens not reported (provider uses no LLM)"}
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ClaimSection({ title, kind, claims, description, onSelectEvidence }: {
  title: string;
  kind: keyof typeof KIND_TONE;
  claims: Claim[];
  description: string;
  onSelectEvidence?: (id: string) => void;
}) {
  if (claims.length === 0) return null;
  const tone = KIND_TONE[kind] ?? KIND_TONE.OBSERVED!;
  return (
    <Panel
      title={
        <span className="row" style={{ gap: 8 }}>
          {title}
          <span
            className="xs"
            style={{
              fontWeight: 700, letterSpacing: "0.05em", color: tone.fg, background: tone.bg,
              border: `1px solid ${tone.border}`, borderRadius: 3, padding: "0 5px",
            }}
          >
            {kind}
          </span>
        </span>
      }
      flush
    >
      <p className="xs dim" style={{ margin: 0, padding: "var(--space-2) var(--space-4) 0" }}>{description}</p>
      <ul style={{ listStyle: "none", margin: 0, padding: "var(--space-2) 0 0" }}>
        {claims.map((claim, index) => (
          <li key={index} style={{ padding: "var(--space-2) var(--space-4)", borderTop: index > 0 ? "1px solid var(--border-hairline)" : "none" }}>
            <p style={{ margin: "0 0 3px", lineHeight: 1.55 }}>{claim.statement}</p>
            <Citations ids={claim.evidence_ids} onSelect={onSelectEvidence} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Citations({ ids, onSelect, label = "evidence" }: {
  ids: string[];
  onSelect?: (id: string) => void;
  label?: string;
}) {
  if (ids.length === 0) {
    return <p className="xs dim" style={{ margin: 0 }}>no citation</p>;
  }
  return (
    <p className="xs" style={{ margin: 0 }}>
      <span className="dim">{label} </span>
      {ids.map((id, index) => (
        <span key={id}>{index > 0 && ", "}<EvidenceLink id={id} onSelect={onSelect} /></span>
      ))}
    </p>
  );
}
