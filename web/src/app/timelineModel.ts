/**
 * Group the raw event stream into the units a reader actually thinks in.
 *
 * The API emits one event per fact, which is right for a contract but wrong for a timeline: a single
 * MCP tool call arrives as three events (requested, completed, evidence registered), and rendering
 * three rows for one action buries the thing that matters. This folds them into one entry showing
 * the request, its outcome and the evidence it produced together.
 *
 * Pure functions over the real payloads. Nothing is invented; grouping only re-arranges.
 */

import type { EventPayloads, InvestigationEvent } from "@/types/api";

export type TimelineEntry =
  | { kind: "opened"; seq: number; at: string; payload: EventPayloads["investigation.created"] }
  | { kind: "status"; seq: number; at: string; payload: EventPayloads["status.changed"] }
  | {
      kind: "step";
      seq: number;
      at: string;
      step: number;
      /** Present once the provider answers; absent while the step is still in flight. */
      provider: EventPayloads["provider.completed"] | null;
      budgetRemaining: Record<string, number>;
    }
  | {
      kind: "action";
      seq: number;
      at: string;
      step: number;
      /** `tool` for an MCP tool call, `resource` for a resource read, `local` for a local action. */
      source: "tool" | "resource" | "local";
      name: string;
      arguments: Record<string, unknown>;
      status: "pending" | "ok" | "error";
      error: string | null;
      latencyMs: number | null;
      evidenceId: string | null;
      recordCount: number | null;
      resultKind: string | null;
    }
  | { kind: "rejected"; seq: number; at: string; payload: EventPayloads["tool.rejected"] }
  | { kind: "hypothesis"; seq: number; at: string; payload: EventPayloads["hypothesis.updated"] }
  | { kind: "validation"; seq: number; at: string; round: number; failed: boolean; errors: number; rules: string[] }
  | { kind: "repair"; seq: number; at: string; payload: EventPayloads["repair.started"] }
  | { kind: "report"; seq: number; at: string; payload: EventPayloads["report.completed"] }
  | { kind: "finished"; seq: number; at: string; payload: EventPayloads["investigation.completed"] }
  | { kind: "failed"; seq: number; at: string; payload: EventPayloads["investigation.failed"] };

/** Local actions have no MCP call behind them; they are the orchestrator's own verbs. */
const LOCAL_ACTIONS = new Set(["update_hypotheses", "finish_investigation", "read_resource"]);

export function buildTimeline(events: InvestigationEvent[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  /** request id -> index in `entries`, so a completion can be folded into its request. */
  const byRequest = new Map<string, number>();
  /** evidence id -> index, so a registration can be folded into the action that produced it. */
  const byEvidence = new Map<string, number>();
  let currentStep = 0;

  for (const event of events) {
    switch (event.type) {
      case "investigation.created":
        entries.push({ kind: "opened", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "status.changed":
        entries.push({ kind: "status", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "step.started":
        currentStep = event.payload.step;
        entries.push({
          kind: "step", seq: event.seq, at: event.at, step: event.payload.step,
          provider: null, budgetRemaining: event.payload.budget_remaining,
        });
        break;

      case "provider.completed": {
        // Fold the provider's answer into the step it belongs to, rather than a second row.
        const index = lastIndex(entries, (entry) => entry.kind === "step" && entry.step === event.payload.step
          && entry.provider === null);
        if (index >= 0) {
          const step = entries[index] as Extract<TimelineEntry, { kind: "step" }>;
          entries[index] = { ...step, provider: event.payload };
        } else {
          // Report and repair calls have no step of their own.
          entries.push({
            kind: "step", seq: event.seq, at: event.at, step: event.payload.step,
            provider: event.payload, budgetRemaining: {},
          });
        }
        break;
      }

      case "tool.requested": {
        const name = event.payload.name;
        entries.push({
          kind: "action", seq: event.seq, at: event.at, step: event.payload.step,
          source: LOCAL_ACTIONS.has(name) ? "local" : "tool",
          name, arguments: event.payload.arguments, status: "pending", error: null,
          latencyMs: null, evidenceId: null, recordCount: null, resultKind: null,
        });
        byRequest.set(event.payload.request_id, entries.length - 1);
        break;
      }

      case "tool.completed": {
        const index = byRequest.get(event.payload.request_id);
        if (index === undefined) break;
        const action = entries[index] as Extract<TimelineEntry, { kind: "action" }>;
        entries[index] = {
          ...action,
          status: event.payload.ok ? "ok" : "error",
          error: event.payload.error,
          latencyMs: event.payload.latency_ms,
          evidenceId: event.payload.evidence_id,
        };
        if (event.payload.evidence_id) byEvidence.set(event.payload.evidence_id, index);
        break;
      }

      case "resource.read": {
        const index = byRequest.get(event.payload.request_id);
        if (index !== undefined) {
          // The read was requested as a local `read_resource` action; enrich that entry.
          const action = entries[index] as Extract<TimelineEntry, { kind: "action" }>;
          entries[index] = {
            ...action, source: "resource", name: event.payload.uri, arguments: {},
            status: event.payload.ok ? "ok" : "error", error: event.payload.error,
            evidenceId: event.payload.evidence_id,
          };
          if (event.payload.evidence_id) byEvidence.set(event.payload.evidence_id, index);
          break;
        }
        entries.push({
          kind: "action", seq: event.seq, at: event.at, step: event.payload.step, source: "resource",
          name: event.payload.uri, arguments: {}, status: event.payload.ok ? "ok" : "error",
          error: event.payload.error, latencyMs: null, evidenceId: event.payload.evidence_id,
          recordCount: null, resultKind: null,
        });
        if (event.payload.evidence_id) byEvidence.set(event.payload.evidence_id, entries.length - 1);
        break;
      }

      case "evidence.registered": {
        // The engine registers evidence *before* it records the tool outcome, so the usual case is
        // an action that is still pending. Fold into it; only genuinely orphan evidence (the seed
        // reads, gathered before the provider was asked anything) gets a row of its own.
        const index = byEvidence.get(event.payload.evidence_id)
          ?? lastIndex(entries, (entry) => entry.kind === "action" && entry.evidenceId === null);
        if (index >= 0 && index !== undefined) {
          const action = entries[index] as Extract<TimelineEntry, { kind: "action" }>;
          entries[index] = {
            ...action, evidenceId: event.payload.evidence_id,
            recordCount: event.payload.record_count, resultKind: event.payload.result_kind,
          };
          byEvidence.set(event.payload.evidence_id, index);
          break;
        }
        entries.push({
          kind: "action", seq: event.seq, at: event.at, step: currentStep,
          source: event.payload.source_kind === "resource" ? "resource" : "tool",
          name: event.payload.source_name, arguments: {}, status: event.payload.ok ? "ok" : "error",
          error: null, latencyMs: null, evidenceId: event.payload.evidence_id,
          recordCount: event.payload.record_count, resultKind: event.payload.result_kind,
        });
        break;
      }

      case "tool.rejected":
        entries.push({ kind: "rejected", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "hypothesis.updated":
        entries.push({ kind: "hypothesis", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "validation.started":
        entries.push({
          kind: "validation", seq: event.seq, at: event.at, round: event.payload.round,
          failed: false, errors: 0, rules: [],
        });
        break;

      case "validation.failed": {
        const index = lastIndex(entries, (entry) => entry.kind === "validation" && entry.round === event.payload.round);
        const failure = {
          kind: "validation" as const, seq: event.seq, at: event.at, round: event.payload.round,
          failed: true, errors: event.payload.errors, rules: event.payload.rules,
        };
        if (index >= 0) entries[index] = failure;
        else entries.push(failure);
        break;
      }

      case "repair.started":
        entries.push({ kind: "repair", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "report.completed":
        entries.push({ kind: "report", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "investigation.completed":
        entries.push({ kind: "finished", seq: event.seq, at: event.at, payload: event.payload });
        break;

      case "investigation.failed":
        entries.push({ kind: "failed", seq: event.seq, at: event.at, payload: event.payload });
        break;
    }
  }
  return entries;
}

function lastIndex(entries: TimelineEntry[], predicate: (entry: TimelineEntry) => boolean): number {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    if (predicate(entries[index]!)) return index;
  }
  return -1;
}

/** How many MCP tool calls the stream has produced, for the timeline header. */
export function countMcpCalls(entries: TimelineEntry[]): number {
  return entries.filter((entry) => entry.kind === "action" && entry.source === "tool").length;
}
