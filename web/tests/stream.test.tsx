/**
 * The SSE client: ordering, de-duplication, reconnection and termination.
 *
 * These are the behaviours that make the live view trustworthy, so they are tested against the real
 * event fixture captured from the API rather than invented frames.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useInvestigationStream } from "@/hooks/useInvestigationStream";
import { hypothesesFromEvents, evidenceFromEvents } from "@/app/fromEvents";
import { FakeEventSource, fixtures, stubEventSource } from "./helpers";
import type { InvestigationEvent } from "@/types/api";

const EVENTS = fixtures.events;

describe("useInvestigationStream", () => {
  beforeEach(() => {
    stubEventSource();
  });

  it("renders events in sequence order and closes on the terminal event", async () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1"));
    const source = FakeEventSource.latest();
    expect(source.url).toContain("/api/investigations/inv-1/events");

    act(() => source.emitOpen());
    await waitFor(() => expect(result.current.status).toBe("open"));

    act(() => {
      for (const event of EVENTS) source.emit(event);
    });

    await waitFor(() => expect(result.current.terminal).not.toBeNull());
    expect(result.current.events).toHaveLength(EVENTS.length);
    expect(result.current.events.map((e) => e.seq)).toEqual(EVENTS.map((e) => e.seq));
    expect(result.current.lastSeq).toBe(EVENTS.at(-1)!.seq);
    expect(result.current.terminal?.type).toBe("investigation.completed");
    expect(result.current.status).toBe("closed");
    expect(source.closed).toBe(true);
  });

  it("sorts out-of-order delivery by sequence", async () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1"));
    const source = FakeEventSource.latest();
    const [first, second, third] = EVENTS as [InvestigationEvent, InvestigationEvent, InvestigationEvent];

    act(() => {
      source.emit(third);
      source.emit(first);
      source.emit(second);
    });

    await waitFor(() => expect(result.current.events).toHaveLength(3));
    expect(result.current.events.map((e) => e.seq)).toEqual([first.seq, second.seq, third.seq]);
  });

  it("drops duplicates, which is what a reconnect replay delivers", async () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1"));
    const source = FakeEventSource.latest();
    const batch = EVENTS.slice(0, 6);

    act(() => {
      for (const event of batch) source.emit(event);
    });
    await waitFor(() => expect(result.current.events).toHaveLength(6));

    // The server replays from the cursor, so frames 4-6 arrive a second time.
    act(() => {
      for (const event of batch.slice(3)) source.emit(event);
    });

    expect(result.current.events).toHaveLength(6);
    expect(new Set(result.current.events.map((e) => e.seq)).size).toBe(6);
  });

  it("tracks the cursor the browser would resend on reconnect", async () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1"));
    const source = FakeEventSource.latest();
    const batch = EVENTS.slice(0, 5);

    act(() => {
      for (const event of batch) source.emit(event);
    });
    await waitFor(() => expect(result.current.lastSeq).toBe(batch.at(-1)!.seq));

    // EventSource sends Last-Event-ID itself; it must be the last sequence we rendered.
    expect(source.lastEventId).toBe(String(batch.at(-1)!.seq));

    act(() => source.emitError());
    await waitFor(() => expect(result.current.status).toBe("reconnecting"));
    expect(result.current.events).toHaveLength(5); // nothing lost while reconnecting

    act(() => {
      for (const event of EVENTS.slice(3)) source.emit(event); // overlap plus the rest
    });
    await waitFor(() => expect(result.current.terminal).not.toBeNull());
    expect(result.current.events.map((e) => e.seq)).toEqual(EVENTS.map((e) => e.seq));
  });

  it("reports a fatal stream failure instead of freezing silently", async () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1"));
    const source = FakeEventSource.latest();
    act(() => source.emit(EVENTS[0]!));
    act(() => source.emitError({ fatal: true }));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toContain("closed unexpectedly");
  });

  it("ignores malformed and unknown frames", async () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1"));
    const source = FakeEventSource.latest();
    act(() => {
      source.emit(EVENTS[0]!);
      // A frame whose payload is not an investigation event must not corrupt the timeline.
      source.emit({ seq: 2, investigation_id: "inv-1", type: "nonsense", at: "", payload: {} } as never);
    });
    await waitFor(() => expect(result.current.events).toHaveLength(1));
  });

  it("does not open a stream without an investigation id", () => {
    renderHook(() => useInvestigationStream(undefined));
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

describe("deriving state from the stream", () => {
  it("builds the hypothesis board with its revision history", () => {
    const hypotheses = hypothesesFromEvents(EVENTS);
    expect(hypotheses.length).toBeGreaterThan(0);

    const evolving = hypotheses.find((h) => h.revisions.length > 1);
    expect(evolving, "at least one hypothesis should change over the investigation").toBeDefined();
    expect(evolving!.revisions.map((r) => r.step)).toEqual([...evolving!.revisions.map((r) => r.step)].sort((a, b) => a - b));

    const last = evolving!.revisions.at(-1)!;
    expect(evolving!.status).toBe(last.status);
    expect(evolving!.confidence).toBe(last.confidence);
    // Sorted strongest first, so the board reads top-down.
    expect(hypotheses.map((h) => h.confidence)).toEqual([...hypotheses.map((h) => h.confidence)].sort((a, b) => b - a));
  });

  it("builds evidence rows flagged untrusted", () => {
    const evidence = evidenceFromEvents(EVENTS);
    expect(evidence.length).toBeGreaterThan(4);
    expect(evidence.every((item) => item.untrusted)).toBe(true);
    expect(evidence.map((item) => item.sequence)).toEqual([...evidence.map((item) => item.sequence)].sort((a, b) => a - b));
  });
});
