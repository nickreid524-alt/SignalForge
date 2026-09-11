/**
 * The observable investigation trace.
 *
 * Named carefully: this is the *observable* record of what the system did, not a chain of thought.
 * Provider reasoning blocks are never recorded by the backend and never appear here.
 */

import { Badge, Collapsible, JsonBlock, Latency, Panel } from "@/components/primitives";
import type { InvestigationTrace } from "@/types/api";

const USAGE_LABELS: [string, string][] = [
  ["steps", "Steps"],
  ["tool_calls", "MCP tool calls"],
  ["resource_reads", "Resource reads"],
  ["model_calls", "Provider calls"],
  ["rejected_actions", "Refused actions"],
  ["suppressed_duplicates", "Duplicates suppressed"],
  ["repair_rounds", "Repair rounds"],
];

export function TraceView({ trace }: { trace: InvestigationTrace }) {
  const inv = trace.investigation;
  const usage = inv.usage ?? {};
  return (
    <div className="stack">
      <Panel title="Observable investigation trace">
        <p className="small muted" style={{ margin: "0 0 var(--space-4)", maxWidth: "76ch", lineHeight: 1.6 }}>
          {trace.notice}
        </p>

        <div className="grid-3" style={{ gap: "var(--space-3) var(--space-5)", marginBottom: "var(--space-4)" }}>
          <Fact label="Provider">
            {inv.provider_name} <span className="dim">({inv.provider_mode})</span>
          </Fact>
          <Fact label="Model">{inv.provider_model ?? "—"}</Fact>
          <Fact label="Uses a language model">{inv.uses_llm ? "yes" : "no"}</Fact>
          <Fact label="MCP transport">{inv.transport}</Fact>
          <Fact label="Started">{clock(inv.started_at)}</Fact>
          <Fact label="Ended">{inv.ended_at ? clock(inv.ended_at) : "running"}</Fact>
        </div>

        <div className="row row--wrap" style={{ gap: "var(--space-5)", paddingTop: "var(--space-3)",
          borderTop: "1px solid var(--border-hairline)" }}>
          {USAGE_LABELS.filter(([key]) => usage[key] !== undefined).map(([key, label]) => (
            <span key={key}>
              <span className="mono" style={{ fontSize: "var(--text-lg)", fontWeight: 600 }}>{usage[key]}</span>
              <span className="xs dim" style={{ display: "block" }}>{label}</span>
            </span>
          ))}
          <span>
            <span className="mono" style={{ fontSize: "var(--text-lg)", fontWeight: 600 }}>
              {usage.tokens_reported ? `${usage.input_tokens ?? 0} / ${usage.output_tokens ?? 0}` : "n/a"}
            </span>
            <span className="xs dim" style={{ display: "block" }}>
              {usage.tokens_reported ? "Tokens in / out" : "Tokens (no language model)"}
            </span>
          </span>
        </div>
      </Panel>

      <Panel title={`State transitions (${trace.status_changes.length})`} flush>
        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {trace.status_changes.map((change, index) => (
            <li key={index} className="row" style={{ padding: "5px var(--space-4)", borderBottom: "1px solid var(--border-hairline)", gap: "var(--space-3)" }}>
              <span className="mono xs dim" style={{ width: 62 }}>{change.at.slice(11, 19)}</span>
              <span className="small muted">{change.from_status}</span>
              <span className="dim" aria-hidden="true">→</span>
              <span className="small" style={{ fontWeight: 600 }}>{change.to_status}</span>
              {change.note && <span className="xs dim">{change.note}</span>}
            </li>
          ))}
        </ol>
      </Panel>

      <Panel title={`Provider calls (${trace.model_calls.length})`} flush>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Call</th><th scope="col">Step</th><th scope="col">Purpose</th>
              <th scope="col">Model</th><th scope="col">Stop</th><th scope="col">Latency</th><th scope="col">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {trace.model_calls.map((call) => (
              <tr key={call.id}>
                <td className="table__id">{call.id.split("-").pop()}</td>
                <td className="table__num">{call.step ?? "—"}</td>
                <td className="small">{call.purpose}</td>
                <td className="mono small">{call.provider_model ?? "—"}</td>
                <td className="small">
                  {call.error_category ? <Badge tone="danger">{call.error_category}</Badge> : call.stop_reason ?? "—"}
                </td>
                <td className="table__num"><Latency ms={call.latency_ms} /></td>
                <td className="table__num small">
                  {call.usage_reported ? `${call.input_tokens} / ${call.output_tokens}` : <span className="dim">n/a</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title={`Requested actions (${trace.actions.length})`} flush>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Step</th><th scope="col">Kind</th><th scope="col">Action</th>
              <th scope="col">Outcome</th><th scope="col">Evidence</th><th scope="col">Latency</th>
            </tr>
          </thead>
          <tbody>
            {trace.actions.map((action, index) => (
              <tr key={index}>
                <td className="table__num">{action.step}</td>
                <td className="small">{action.kind.replace(/_/g, " ")}</td>
                <td className="mono small">
                  {action.name}
                  {Object.keys(action.arguments).length > 0 && (
                    <Collapsible summary="arguments"><JsonBlock value={action.arguments} maxHeight={180} /></Collapsible>
                  )}
                </td>
                <td className="small">
                  {!action.accepted ? (
                    <span>
                      <Badge tone="danger">refused</Badge>{" "}
                      <span className="xs dim">{action.rejection_code}</span>
                    </span>
                  ) : action.ok === false ? (
                    <Badge tone="danger">error</Badge>
                  ) : (
                    <Badge tone="ok">ok</Badge>
                  )}
                </td>
                <td className="table__id">{action.evidence_id ?? <span className="dim">—</span>}</td>
                <td className="table__num"><Latency ms={action.latency_ms} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      {trace.hypothesis_updates.length > 0 && (
        <Panel title={`Hypothesis updates (${trace.hypothesis_updates.length})`} flush>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Step</th><th scope="col">Hypothesis</th><th scope="col">Status</th>
                <th scope="col">Confidence</th><th scope="col">Statement</th>
              </tr>
            </thead>
            <tbody>
              {trace.hypothesis_updates.map((update, index) => (
                <tr key={index}>
                  <td className="table__num">{update.step}</td>
                  <td className="table__id">{update.hypothesis_id}</td>
                  <td className="small">{update.status}</td>
                  <td className="table__num">{update.confidence.toFixed(2)}</td>
                  <td className="small" style={{ maxWidth: 520 }}>{update.statement}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      <Panel title={`Validation rounds (${trace.validations.length})`} flush>
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {trace.validations.map((round) => (
            <li key={round.round} style={{ padding: "var(--space-3) var(--space-4)", borderBottom: "1px solid var(--border-hairline)" }}>
              <div className="row" style={{ gap: 8 }}>
                <span className="small" style={{ fontWeight: 600 }}>Round {round.round}</span>
                <Badge tone={round.ok ? "ok" : "danger"}>{round.ok ? "passed" : "failed"}</Badge>
                <span className="xs dim">{round.error_count} errors · {round.warning_count} warnings</span>
              </div>
              {round.issues.length > 0 && (
                <ul className="xs" style={{ margin: "4px 0 0", paddingLeft: "var(--space-4)" }}>
                  {round.issues.map((issue, index) => (
                    <li key={index} className={issue.severity === "error" ? "" : "dim"}>
                      <span className="mono">{issue.rule}</span> {issue.message}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
          {trace.repairs.map((repair) => (
            <li key={`repair-${repair.round}`} style={{ padding: "var(--space-3) var(--space-4)" }}>
              <span className="small" style={{ fontWeight: 600 }}>Repair request {repair.round}</span>
              <p className="xs muted" style={{ margin: "3px 0 0" }}>{repair.request_text.slice(0, 400)}</p>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="xs dim">{label}</div>
      <div className="mono small" style={{ marginTop: 1 }}>{children}</div>
    </div>
  );
}

/** Trace timestamps carry microseconds; a reader needs the time, not the precision. */
function clock(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : `${date.toISOString().slice(0, 19).replace("T", " ")}Z`;
}
