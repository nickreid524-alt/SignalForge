/**
 * The timeline grouping.
 *
 * One MCP call arrives as three events; the timeline must show it as one entry. This is checked
 * against the real captured stream, so a change to the event order in the backend would fail here
 * rather than quietly producing duplicate rows.
 */

import { describe, expect, it } from "vitest";
import { buildTimeline, countMcpCalls } from "@/app/timelineModel";
import { fixtures } from "./helpers";
import type { InvestigationEvent } from "@/types/api";

const EVENTS = fixtures.events;

describe("buildTimeline", () => {
  it("collapses request, evidence and completion into one action entry", () => {
    const entries = buildTimeline(EVENTS);
    const actions = entries.filter((entry) => entry.kind === "action");

    // Every tool call in the stream produces exactly one entry, and no evidence id appears twice.
    const evidenceIds = actions.map((a) => a.evidenceId).filter((id): id is string => id !== null);
    expect(new Set(evidenceIds).size).toBe(evidenceIds.length);

    const registered = EVENTS.filter((e) => e.type === "evidence.registered");
    expect(evidenceIds).toHaveLength(registered.length);

    // Each carries the request, the outcome and the evidence together.
    const toolCalls = actions.filter((a) => a.source === "tool" && Object.keys(a.arguments).length > 0);
    expect(toolCalls.length).toBeGreaterThan(0);
    for (const call of toolCalls) {
      expect(call.status).toBe("ok");
      expect(call.evidenceId).toMatch(/^EVD-\d{6}$/);
      expect(call.recordCount).not.toBeNull();
      expect(call.latencyMs).not.toBeNull();
    }
  });

  it("folds the provider's answer into the step it belongs to", () => {
    const entries = buildTimeline(EVENTS);
    const steps = entries.filter((entry) => entry.kind === "step");
    const providerEvents = EVENTS.filter((e) => e.type === "provider.completed");

    expect(steps.length).toBe(providerEvents.length);
    for (const step of steps) {
      expect(step.provider).not.toBeNull();
    }
    const deliberations = steps.filter((s) => s.provider?.purpose === "deliberate");
    expect(deliberations.length).toBeGreaterThan(0);
    expect(deliberations[0]!.provider!.tool_requests).toBeGreaterThan(0);
  });

  it("gives seed evidence its own entry, because no action produced it", () => {
    const entries = buildTimeline(EVENTS);
    const actions = entries.filter((entry) => entry.kind === "action");
    const seed = actions.filter((a) => a.source === "resource" && Object.keys(a.arguments).length === 0);

    expect(seed.length).toBeGreaterThanOrEqual(2);
    expect(seed[0]!.name).toBe("incidents://open");
    expect(seed[0]!.evidenceId).toBe("EVD-000001");
    expect(seed[0]!.recordCount).toBeGreaterThan(0);
  });

  it("preserves stream order and reports the MCP call count", () => {
    const entries = buildTimeline(EVENTS);
    const seqs = entries.map((entry) => entry.seq);
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(entries[0]!.kind).toBe("opened");
    expect(entries.at(-1)!.kind).toBe("finished");

    const finished = EVENTS.find((e) => e.type === "investigation.completed")!;
    expect(countMcpCalls(entries)).toBe((finished.payload as { tool_calls: number }).tool_calls);
  });

  it("keeps a refused action visible as its own entry", () => {
    const rejected: InvestigationEvent = {
      seq: 99, investigation_id: "inv-x", type: "tool.rejected", at: new Date().toISOString(),
      payload: { step: 2, request_id: "t9", name: "disable_fraud_checks", code: "unknown_action",
                 reason: "not an allowed tool", duplicate_of: null },
    };
    const entries = buildTimeline([...EVENTS.slice(0, 8), rejected]);
    expect(entries.filter((entry) => entry.kind === "rejected")).toHaveLength(1);
  });

  it("marks an action still in flight as pending", () => {
    const requested = EVENTS.find((e) => e.type === "tool.requested")!;
    const entries = buildTimeline([requested]);
    const action = entries.find((entry) => entry.kind === "action");
    expect(action).toBeDefined();
    expect(action!.status).toBe("pending");
    expect(action!.evidenceId).toBeNull();
  });

  it("handles an empty stream", () => {
    expect(buildTimeline([])).toEqual([]);
    expect(countMcpCalls([])).toBe(0);
  });
});
