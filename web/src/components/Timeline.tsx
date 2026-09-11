/**
 * The investigation timeline.
 *
 * This is the screen that has to communicate the architecture in a few seconds: the provider decides
 * what evidence to request, and MCP supplies it. So an MCP call renders as one operational entry
 * carrying its request, its outcome and the evidence it produced, rather than three separate rows,
 * and the four kinds of activity are visually distinct.
 *
 * Every value comes from the real event payload. Nothing here is synthesised for effect.
 */

import type { ReactNode } from "react";
import { Badge } from "@/components/primitives";
import type { TimelineEntry } from "@/app/timelineModel";
import { buildTimeline } from "@/app/timelineModel";
import type { InvestigationEvent } from "@/types/api";

type Kind = "mcp" | "resource" | "provider" | "hypothesis" | "state" | "validation" | "rejected";

const KIND: Record<Kind, { label: string; fg: string; bg: string; border: string }> = {
  mcp: { label: "MCP TOOL", fg: "var(--accent-strong)", bg: "var(--accent-soft)", border: "var(--accent-border)" },
  resource: { label: "RESOURCE", fg: "#4a5b8c", bg: "#eef1fa", border: "#c8d2ea" },
  provider: { label: "PROVIDER", fg: "#5b636d", bg: "#eef0f2", border: "#d7dbe0" },
  hypothesis: { label: "HYPOTHESIS", fg: "#6b4a8c", bg: "#f4eefa", border: "#dcc8ea" },
  validation: { label: "VALIDATION", fg: "var(--ok)", bg: "var(--ok-bg)", border: "var(--ok-border)" },
  rejected: { label: "REFUSED", fg: "var(--danger)", bg: "var(--danger-bg)", border: "var(--danger-border)" },
  state: { label: "STATE", fg: "var(--fg-dim)", bg: "transparent", border: "var(--border-hairline)" },
};

export function Timeline({ events, onSelectEvidence }: {
  events: InvestigationEvent[];
  onSelectEvidence?: (evidenceId: string) => void;
}) {
  const entries = buildTimeline(events);
  return (
    <ol className="tl" aria-label="Investigation timeline">
      {entries.map((entry) => (
        <li key={`${entry.kind}-${entry.seq}`}>
          <Entry entry={entry} onSelectEvidence={onSelectEvidence} />
        </li>
      ))}
    </ol>
  );
}

function Entry({ entry, onSelectEvidence }: {
  entry: TimelineEntry;
  onSelectEvidence?: (id: string) => void;
}) {
  switch (entry.kind) {
    case "opened":
      return (
        <Row kind="state" seq={entry.seq} at={entry.at} quiet
          heading={<>Investigation opened for <span className="mono">{entry.payload.incident_id}</span></>}
          meta={<>
            <Badge>{entry.payload.provider}</Badge>
            {entry.payload.uses_live_api ? <Badge tone="live">LIVE API</Badge> : <Badge tone="ok">NO LLM</Badge>}
          </>}
        />
      );

    case "status":
      return (
        <Row kind="state" seq={entry.seq} at={entry.at} quiet
          heading={
            <span className="dim">
              {entry.payload.from_status} <span aria-hidden="true">→</span>{" "}
              <span style={{ color: "var(--fg-muted)", fontWeight: 600 }}>{entry.payload.to_status}</span>
            </span>
          }
        />
      );

    case "step": {
      const provider = entry.provider;
      const purpose = provider?.purpose ?? "deliberate";
      return (
        <Row kind="provider" seq={entry.seq} at={entry.at}
          heading={
            purpose === "deliberate"
              ? <>Step {entry.step} · provider chose{" "}
                  <strong>{provider ? provider.tool_requests : "…"}</strong>{" "}
                  {provider?.tool_requests === 1 ? "action" : "actions"}</>
              : purpose === "report" ? <>Provider produced the structured report</>
              : <>Provider revised the report</>
          }
          meta={provider && <>
            {provider.stop_reason && <span className="tl__meta mono">stop {provider.stop_reason}</span>}
            <span className="tl__meta mono">{formatMs(provider.latency_ms)}</span>
            {provider.usage_reported && (
              <span className="tl__meta mono">{provider.input_tokens} in / {provider.output_tokens} out</span>
            )}
          </>}
        >
          {provider?.text_preview && <p className="tl__note">{provider.text_preview}</p>}
        </Row>
      );
    }

    case "action": {
      const isTool = entry.source === "tool";
      const kind: Kind = entry.source === "resource" ? "resource" : isTool ? "mcp" : "provider";
      return (
        <Row kind={kind} seq={entry.seq} at={entry.at}
          heading={<span className="tl__command mono">{entry.name}</span>}
          meta={<>
            {entry.status === "pending" && <Badge>requested…</Badge>}
            {entry.status === "ok" && <Badge tone="ok">ok</Badge>}
            {entry.status === "error" && <Badge tone="danger">failed</Badge>}
            {entry.latencyMs !== null && <span className="tl__meta mono">{formatMs(entry.latencyMs)}</span>}
          </>}
        >
          {Object.keys(entry.arguments).length > 0 && <Arguments args={entry.arguments} />}
          {entry.error && <p className="tl__note" style={{ color: "var(--danger)" }}>{entry.error}</p>}
          {entry.evidenceId && (
            <p className="tl__result">
              <span className="dim" aria-hidden="true">→ </span>
              <EvidenceLink id={entry.evidenceId} onSelect={onSelectEvidence} strong />
              {entry.recordCount !== null && (
                <span className="tl__meta">
                  {entry.recordCount} {entry.recordCount === 1 ? "record" : "records"}
                </span>
              )}
              {entry.resultKind && <span className="tl__meta">{entry.resultKind}</span>}
              <Badge tone="warn" title="Retrieved content is data, never instructions">UNTRUSTED</Badge>
            </p>
          )}
        </Row>
      );
    }

    case "rejected":
      return (
        <Row kind="rejected" seq={entry.seq} at={entry.at}
          heading={<span className="tl__command mono">{entry.payload.name}</span>}
          meta={<Badge tone="danger">{entry.payload.code.replace(/_/g, " ")}</Badge>}
        >
          <p className="tl__note" style={{ color: "var(--danger)" }}>
            {entry.payload.reason}
            {entry.payload.duplicate_of && <span className="dim"> (already gathered as {entry.payload.duplicate_of})</span>}
          </p>
        </Row>
      );

    case "hypothesis":
      return (
        <Row kind="hypothesis" seq={entry.seq} at={entry.at}
          heading={
            <span className="row" style={{ gap: 7 }}>
              <span className="mono" style={{ fontWeight: 700 }}>{entry.payload.hypothesis_id}</span>
              <StatusPill status={entry.payload.status} />
              <span className="mono tl__meta">{entry.payload.confidence.toFixed(2)}</span>
            </span>
          }
        >
          <p className="tl__note" style={{ color: "var(--fg)" }}>{entry.payload.statement}</p>
          {entry.payload.supporting_evidence_ids.length > 0 && (
            <p className="tl__result">
              <span className="dim">supported by </span>
              <Citations ids={entry.payload.supporting_evidence_ids} onSelect={onSelectEvidence} />
            </p>
          )}
          {entry.payload.contradicting_evidence_ids.length > 0 && (
            <p className="tl__result">
              <span className="dim">contradicted by </span>
              <Citations ids={entry.payload.contradicting_evidence_ids} onSelect={onSelectEvidence} />
            </p>
          )}
        </Row>
      );

    case "validation":
      return (
        <Row kind={entry.failed ? "rejected" : "validation"} seq={entry.seq} at={entry.at}
          heading={entry.failed
            ? <>Grounding validation failed, round {entry.round}</>
            : <>Grounding validation, round {entry.round}</>}
          meta={entry.failed && <>
            <Badge tone="danger">{entry.errors} error{entry.errors === 1 ? "" : "s"}</Badge>
            <span className="tl__meta mono">{entry.rules.join(", ")}</span>
          </>}
        />
      );

    case "repair":
      return (
        <Row kind="validation" seq={entry.seq} at={entry.at} heading={<>Repair round {entry.payload.round}</>}>
          <p className="tl__note">{entry.payload.reason}</p>
        </Row>
      );

    case "report":
      return (
        <Row kind="validation" seq={entry.seq} at={entry.at} heading={<strong>Report completed</strong>}
          meta={<>
            <Badge tone={entry.payload.validation_ok ? "ok" : "danger"}>
              {entry.payload.validation_ok ? "grounding validated" : "validation errors"}
            </Badge>
            <span className="tl__meta mono">confidence {entry.payload.confidence.toFixed(2)}</span>
          </>}
        />
      );

    case "finished":
      return (
        <Row kind="state" seq={entry.seq} at={entry.at}
          heading={<strong>Investigation {entry.payload.terminal_status.replace(/_/g, " ")}</strong>}
          meta={
            <span className="tl__meta mono">
              {entry.payload.steps} steps · {entry.payload.tool_calls} tool calls ·{" "}
              {entry.payload.evidence_count} evidence
              {entry.payload.rejected_actions > 0 && ` · ${entry.payload.rejected_actions} refused`}
            </span>
          }
        />
      );

    case "failed":
      return (
        <Row kind="rejected" seq={entry.seq} at={entry.at} heading={<strong>Investigation failed</strong>}
          meta={entry.payload.category && <Badge tone="danger">{entry.payload.category}</Badge>}
        >
          <p className="tl__note" style={{ color: "var(--danger)" }}>{entry.payload.message}</p>
        </Row>
      );
  }
}

function Row({ kind, seq, at, heading, meta, quiet = false, children }: {
  kind: Kind;
  seq: number;
  at: string;
  heading: ReactNode;
  meta?: ReactNode;
  quiet?: boolean;
  children?: ReactNode;
}) {
  const style = KIND[kind];
  return (
    <div className={`tl__row${quiet ? " tl__row--quiet" : ""}`}>
      <div className="tl__gutter">
        <span className="tl__tag" style={{ color: style.fg, background: style.bg, borderColor: style.border }}>
          {style.label}
        </span>
        <span className="tl__seq mono">#{seq} · {at.slice(11, 19)}</span>
      </div>
      <div className="tl__body">
        <div className="tl__head">
          <span className={quiet ? "tl__heading tl__heading--quiet" : "tl__heading"}>{heading}</span>
          {meta}
        </div>
        {children}
      </div>
    </div>
  );
}

function Arguments({ args }: { args: Record<string, unknown> }) {
  return (
    <dl className="tl__args">
      {Object.entries(args).map(([key, value]) => (
        <div key={key} className="tl__arg">
          <dt className="mono">{key}</dt>
          <dd className="mono">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Citations({ ids, onSelect }: { ids: string[]; onSelect?: (id: string) => void }) {
  return (
    <>
      {ids.map((id, index) => (
        <span key={id}>
          {index > 0 && <span className="dim">, </span>}
          <EvidenceLink id={id} onSelect={onSelect} />
        </span>
      ))}
    </>
  );
}

export function EvidenceLink({ id, onSelect, strong = false }: {
  id: string;
  onSelect?: (id: string) => void;
  strong?: boolean;
}) {
  // Citations may be narrowed to one record: EVD-000004#DEP-0038 points at DEP-0038 inside EVD-000004.
  const base = id.split("#")[0] ?? id;
  if (!onSelect) return <span className="mono evd">{id}</span>;
  return (
    <button
      type="button"
      className={strong ? "evd evd--link evd--strong mono" : "evd evd--link mono"}
      onClick={() => onSelect(base)}
      title={`Open evidence ${base}`}
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
  return <span className="pill" style={{ color: colour[status] ?? "var(--fg-muted)" }}>{status}</span>;
}

function formatMs(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
