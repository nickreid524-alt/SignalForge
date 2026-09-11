/**
 * The investigation timeline.
 *
 * This is the screen that has to communicate the architecture in a few seconds: the provider decides
 * what evidence to request, and MCP supplies it. So the four kinds of activity are visually
 * distinct, and an MCP tool call is rendered as an operational command with its arguments, latency
 * and the evidence id it produced, not as a line of chat.
 *
 * Every value comes from the real event payload. Nothing here is synthesised for effect.
 */

import type { ReactNode } from "react";
import { Badge, Latency } from "@/components/primitives";
import type { InvestigationEvent } from "@/types/api";

type Kind = "mcp" | "resource" | "provider" | "hypothesis" | "lifecycle" | "validation" | "rejected";

const KIND_STYLE: Record<Kind, { label: string; colour: string; bg: string; border: string }> = {
  mcp: { label: "MCP TOOL", colour: "var(--accent-strong)", bg: "var(--accent-soft)", border: "var(--accent-border)" },
  resource: { label: "RESOURCE READ", colour: "#4a5b8c", bg: "#eef1fa", border: "#c8d2ea" },
  provider: { label: "PROVIDER STEP", colour: "#5b636d", bg: "var(--surface-sunken)", border: "var(--border)" },
  hypothesis: { label: "HYPOTHESIS", colour: "#6b4a8c", bg: "#f4eefa", border: "#dcc8ea" },
  validation: { label: "VALIDATION", colour: "var(--ok)", bg: "var(--ok-bg)", border: "var(--ok-border)" },
  rejected: { label: "REFUSED", colour: "var(--danger)", bg: "var(--danger-bg)", border: "var(--danger-border)" },
  lifecycle: { label: "STATE", colour: "var(--fg-muted)", bg: "transparent", border: "var(--border-hairline)" },
};

export function Timeline({ events, onSelectEvidence }: {
  events: InvestigationEvent[];
  onSelectEvidence?: (evidenceId: string) => void;
}) {
  return (
    <ol style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Investigation timeline">
      {events.map((event) => {
        const entry = render(event, onSelectEvidence);
        if (!entry) return null;
        return (
          <li key={event.seq}>
            <Entry kind={entry.kind} seq={event.seq} at={event.at} heading={entry.heading} meta={entry.meta}>
              {entry.body}
            </Entry>
          </li>
        );
      })}
    </ol>
  );
}

function Entry({ kind, seq, at, heading, meta, children }: {
  kind: Kind;
  seq: number;
  at: string;
  heading: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
}) {
  const style = KIND_STYLE[kind];
  const quiet = kind === "lifecycle";
  return (
    <div
      style={{
        display: "grid", gridTemplateColumns: "86px 1fr", gap: "var(--space-3)",
        padding: quiet ? "3px var(--space-4)" : "var(--space-3) var(--space-4)",
        borderBottom: "1px solid var(--border-hairline)",
        background: quiet ? "transparent" : "var(--surface)",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 3 }}>
        <span
          className="xs"
          style={{
            fontWeight: 700, letterSpacing: "0.05em", color: style.colour, background: style.bg,
            border: `1px solid ${style.border}`, borderRadius: 3, padding: "0 5px", whiteSpace: "nowrap",
          }}
        >
          {style.label}
        </span>
        <span className="mono dim" style={{ fontSize: 10 }}>
          #{seq} · {at.slice(11, 19)}
        </span>
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="row row--wrap" style={{ gap: "var(--space-2)" }}>
          <span style={{ fontWeight: quiet ? 400 : 600, fontSize: quiet ? "var(--text-sm)" : "var(--text-base)" }}>
            {heading}
          </span>
          {meta}
        </div>
        {children && <div style={{ marginTop: 5 }}>{children}</div>}
      </div>
    </div>
  );
}

interface Rendered {
  kind: Kind;
  heading: ReactNode;
  meta?: ReactNode;
  body?: ReactNode;
}

function render(event: InvestigationEvent, onSelectEvidence?: (id: string) => void): Rendered | null {
  switch (event.type) {
    case "investigation.created": {
      const p = event.payload;
      return {
        kind: "lifecycle",
        heading: `Investigation opened for ${p.incident_id}`,
        meta: (
          <>
            <Badge>{p.provider}</Badge>
            {p.uses_live_api ? <Badge tone="live">LIVE API</Badge> : <Badge tone="ok">NO LLM</Badge>}
            <Badge>{p.budget_profile} budget</Badge>
          </>
        ),
      };
    }
    case "status.changed":
      return {
        kind: "lifecycle",
        heading: (
          <span className="muted">
            {event.payload.from_status} <span className="dim">→</span>{" "}
            <strong style={{ fontWeight: 600 }}>{event.payload.to_status}</strong>
          </span>
        ),
        meta: event.payload.note ? <span className="xs dim">{event.payload.note}</span> : undefined,
      };
    case "step.started":
      return {
        kind: "lifecycle",
        heading: <span className="muted">Step {event.payload.step} started</span>,
        meta: (
          <span className="xs dim mono">
            budget left: {Object.entries(event.payload.budget_remaining)
              .map(([key, value]) => `${key.replace(/_/g, " ")} ${value}`)
              .join(" · ")}
          </span>
        ),
      };
    case "provider.completed": {
      const p = event.payload;
      return {
        kind: "provider",
        heading: p.purpose === "deliberate"
          ? `Provider chose ${p.tool_requests} action${p.tool_requests === 1 ? "" : "s"}`
          : p.purpose === "report"
            ? "Provider produced the structured report"
            : "Provider revised the report",
        meta: (
          <>
            <span className="xs dim mono">step {p.step}</span>
            {p.stop_reason && <span className="xs dim mono">stop: {p.stop_reason}</span>}
            <Latency ms={p.latency_ms} />
            {p.usage_reported && (
              <span className="xs dim mono">{p.input_tokens} in / {p.output_tokens} out tokens</span>
            )}
          </>
        ),
        body: p.text_preview ? <p className="small muted" style={{ margin: 0 }}>{p.text_preview}</p> : undefined,
      };
    }
    case "tool.requested": {
      const p = event.payload;
      return {
        kind: "mcp",
        heading: <span className="mono" style={{ fontSize: "var(--text-md)" }}>{p.name}</span>,
        meta: <span className="xs dim mono">requested · step {p.step}</span>,
        body: <Arguments args={p.arguments} />,
      };
    }
    case "tool.completed": {
      const p = event.payload;
      return {
        kind: "mcp",
        heading: (
          <span className="row" style={{ gap: 8 }}>
            <span className="mono">{p.name}</span>
            {p.ok ? <Badge tone="ok">completed</Badge> : <Badge tone="danger">failed</Badge>}
          </span>
        ),
        meta: (
          <>
            {p.evidence_id && <EvidenceLink id={p.evidence_id} onSelect={onSelectEvidence} />}
            <Latency ms={p.latency_ms} />
          </>
        ),
        body: p.error ? <p className="small" style={{ margin: 0, color: "var(--danger)" }}>{p.error}</p> : undefined,
      };
    }
    case "tool.rejected": {
      const p = event.payload;
      return {
        kind: "rejected",
        heading: <span className="mono">{p.name}</span>,
        meta: <Badge tone="danger">{p.code.replace(/_/g, " ")}</Badge>,
        body: (
          <p className="small" style={{ margin: 0, color: "var(--danger)" }}>
            {p.reason}
            {p.duplicate_of && <span className="dim"> (already gathered as {p.duplicate_of})</span>}
          </p>
        ),
      };
    }
    case "resource.read": {
      const p = event.payload;
      return {
        kind: "resource",
        heading: <span className="mono">{p.uri}</span>,
        meta: (
          <>
            {p.ok ? <Badge tone="ok">read</Badge> : <Badge tone="danger">failed</Badge>}
            {p.evidence_id && <EvidenceLink id={p.evidence_id} onSelect={onSelectEvidence} />}
          </>
        ),
      };
    }
    case "evidence.registered": {
      const p = event.payload;
      return {
        kind: p.source_kind === "resource" ? "resource" : "mcp",
        heading: (
          <span className="row" style={{ gap: 8 }}>
            <EvidenceLink id={p.evidence_id} onSelect={onSelectEvidence} strong />
            <span className="muted small">registered from</span>
            <span className="mono small">{p.source_name}</span>
          </span>
        ),
        meta: (
          <>
            <span className="xs dim">{p.record_count} record{p.record_count === 1 ? "" : "s"}</span>
            <Badge tone="warn" title="Retrieved content is data, never instructions">UNTRUSTED</Badge>
          </>
        ),
      };
    }
    case "hypothesis.updated": {
      const p = event.payload;
      return {
        kind: "hypothesis",
        heading: (
          <span className="row" style={{ gap: 8 }}>
            <span className="mono" style={{ fontWeight: 700 }}>{p.hypothesis_id}</span>
            <StatusPill status={p.status} />
            <span className="mono small">{p.confidence.toFixed(2)}</span>
          </span>
        ),
        body: (
          <>
            <p className="small" style={{ margin: 0 }}>{p.statement}</p>
            {p.supporting_evidence_ids.length > 0 && (
              <p className="xs dim" style={{ margin: "4px 0 0" }}>
                supported by{" "}
                {p.supporting_evidence_ids.map((id, index) => (
                  <span key={id}>
                    {index > 0 && ", "}
                    <EvidenceLink id={id} onSelect={onSelectEvidence} />
                  </span>
                ))}
              </p>
            )}
          </>
        ),
      };
    }
    case "validation.started":
      return { kind: "validation", heading: `Grounding validation, round ${event.payload.round}` };
    case "validation.failed":
      return {
        kind: "rejected",
        heading: `Validation failed, round ${event.payload.round}`,
        meta: <span className="xs mono">{event.payload.rules.join(", ")}</span>,
        body: (
          <p className="small" style={{ margin: 0 }}>
            {event.payload.errors} grounding error{event.payload.errors === 1 ? "" : "s"}; the report goes back for repair.
          </p>
        ),
      };
    case "repair.started":
      return { kind: "validation", heading: `Repair round ${event.payload.round}`, body: (
        <p className="small muted" style={{ margin: 0 }}>{event.payload.reason}</p>
      ) };
    case "report.completed": {
      const p = event.payload;
      return {
        kind: "validation",
        heading: "Report completed",
        meta: (
          <>
            <Badge tone={p.validation_ok ? "ok" : "danger"}>
              {p.validation_ok ? "grounding validated" : "validation errors"}
            </Badge>
            <span className="mono small">confidence {p.confidence.toFixed(2)}</span>
          </>
        ),
      };
    }
    case "investigation.completed": {
      const p = event.payload;
      return {
        kind: "lifecycle",
        heading: <strong>Investigation {p.terminal_status.replace(/_/g, " ")}</strong>,
        meta: (
          <span className="xs dim mono">
            {p.steps} steps · {p.tool_calls} tool calls · {p.evidence_count} evidence items
            {p.rejected_actions > 0 && ` · ${p.rejected_actions} refused`}
          </span>
        ),
      };
    }
    case "investigation.failed":
      return {
        kind: "rejected",
        heading: <strong>Investigation failed</strong>,
        meta: event.payload.category ? <Badge tone="danger">{event.payload.category}</Badge> : undefined,
        body: <p className="small" style={{ margin: 0 }}>{event.payload.message}</p>,
      };
    default:
      return null;
  }
}

function Arguments({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) return null;
  return (
    <dl
      style={{
        margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "1px var(--space-3)",
        fontSize: "var(--text-sm)",
      }}
    >
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: "contents" }}>
          <dt className="xs dim mono" style={{ textAlign: "right" }}>{key}</dt>
          <dd className="mono" style={{ margin: 0, wordBreak: "break-word" }}>
            {typeof value === "string" ? value : JSON.stringify(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function EvidenceLink({ id, onSelect, strong = false }: {
  id: string;
  onSelect?: (id: string) => void;
  strong?: boolean;
}) {
  // Citations may be narrowed to one record: EVD-000004#DEP-0038 points at DEP-0038 inside EVD-000004.
  const base = id.split("#")[0] ?? id;
  if (!onSelect) return <span className="mono small">{id}</span>;
  return (
    <button
      type="button"
      className="mono"
      onClick={() => onSelect(base)}
      title={`Open evidence ${base}`}
      style={{
        background: "none", border: "none", padding: 0, color: "var(--accent)",
        fontSize: strong ? "var(--text-base)" : "var(--text-sm)", fontWeight: strong ? 700 : 500,
        textDecoration: "underline", textUnderlineOffset: 2,
      }}
    >
      {id}
    </button>
  );
}

export function StatusPill({ status }: { status: string }) {
  const colour: Record<string, string> = {
    proposed: "var(--h-proposed)",
    supported: "var(--h-supported)",
    weakened: "var(--h-weakened)",
    refuted: "var(--h-refuted)",
    inconclusive: "var(--h-proposed)",
  };
  return (
    <span
      className="xs"
      style={{
        fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase",
        color: colour[status] ?? "var(--fg-muted)", border: `1px solid currentColor`,
        borderRadius: 3, padding: "0 5px", opacity: 0.95,
      }}
    >
      {status}
    </span>
  );
}
