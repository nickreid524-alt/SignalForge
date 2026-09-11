/**
 * Derive view state from the live event stream.
 *
 * While an investigation runs the SSE stream is the only source of truth the browser has, so these
 * folds let the hypothesis board and the evidence list populate in real time. Once the API has the
 * fuller record, screens prefer that. Pure functions, kept out of component files so they are easy
 * to test on fixtures.
 */

import type { EvidenceSummary, Hypothesis, InvestigationEvent } from "@/types/api";

/** Fold `hypothesis.updated` events into the same shape the API returns. */
export function hypothesesFromEvents(events: InvestigationEvent[]): Hypothesis[] {
  const byId = new Map<string, Hypothesis>();
  for (const event of events) {
    if (event.type !== "hypothesis.updated") continue;
    const p = event.payload;
    const revision = {
      step: p.step,
      status: p.status,
      confidence: p.confidence,
      supporting_evidence_ids: p.supporting_evidence_ids,
      contradicting_evidence_ids: p.contradicting_evidence_ids,
      note: p.note,
    };
    const existing = byId.get(p.hypothesis_id);
    if (existing) {
      existing.statement = p.statement;
      existing.status = p.status;
      existing.confidence = p.confidence;
      existing.supporting_evidence_ids = p.supporting_evidence_ids;
      existing.contradicting_evidence_ids = p.contradicting_evidence_ids;
      existing.updated_step = p.step;
      existing.revisions = [...existing.revisions, revision];
    } else {
      byId.set(p.hypothesis_id, {
        id: p.hypothesis_id,
        statement: p.statement,
        status: p.status,
        confidence: p.confidence,
        supporting_evidence_ids: p.supporting_evidence_ids,
        contradicting_evidence_ids: p.contradicting_evidence_ids,
        created_step: p.step,
        updated_step: p.step,
        revisions: [revision],
      });
    }
  }
  return [...byId.values()].sort((a, b) => b.confidence - a.confidence || a.id.localeCompare(b.id));
}

/**
 * Fold `evidence.registered` events into evidence rows, so the workbench fills while the
 * investigation runs. These carry less than the API's version; screens swap to that once it loads.
 */
export function evidenceFromEvents(events: InvestigationEvent[]): EvidenceSummary[] {
  const rows: EvidenceSummary[] = [];
  for (const event of events) {
    if (event.type !== "evidence.registered") continue;
    const p = event.payload;
    rows.push({
      evidence_id: p.evidence_id,
      sequence: p.sequence,
      acquired_at: event.at,
      source_kind: p.source_kind === "resource" ? "resource" : "tool",
      source_name: p.source_name,
      arguments: {},
      ok: p.ok,
      error: null,
      result_kind: p.result_kind,
      source_ids: [],
      content_hash: "",
      latency_ms: 0,
      untrusted: true,
    });
  }
  return rows;
}

/** The record count an event reports, which the folded row cannot carry in `source_ids`. */
export function recordCounts(events: InvestigationEvent[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const event of events) {
    if (event.type === "evidence.registered") counts.set(event.payload.evidence_id, event.payload.record_count);
  }
  return counts;
}
