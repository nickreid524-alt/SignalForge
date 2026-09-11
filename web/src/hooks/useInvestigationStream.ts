/**
 * The live investigation stream.
 *
 * `EventSource` handles reconnection and `Last-Event-ID` itself: it remembers the id of the last
 * frame it saw and sends it on reconnect, which is exactly the cursor the API replays from. This
 * hook adds what the browser does not do for us:
 *
 *  - de-duplication by sequence, so a replayed overlap never renders twice;
 *  - ordered accumulation, so out-of-order delivery could never scramble the timeline;
 *  - closing the stream on the terminal event instead of reconnecting forever;
 *  - a visible connection state, so the UI can say "reconnecting" rather than silently freezing.
 *
 * There is no second event protocol here. Everything comes from the API's SSE contract.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { eventStreamUrl } from "@/api/client";
import { EVENT_TYPES, TERMINAL_EVENTS } from "@/types/api";
import type { EventType, InvestigationEvent } from "@/types/api";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed" | "error";

export interface StreamState {
  events: InvestigationEvent[];
  status: StreamStatus;
  lastSeq: number;
  /** Set when the stream ended because the investigation reached a terminal event. */
  terminal: InvestigationEvent | null;
  error: string | null;
}

const KNOWN: ReadonlySet<string> = new Set(EVENT_TYPES);

function isInvestigationEvent(value: unknown): value is InvestigationEvent {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.seq === "number" &&
    typeof candidate.investigation_id === "string" &&
    typeof candidate.type === "string" &&
    KNOWN.has(candidate.type) &&
    typeof candidate.at === "string" &&
    typeof candidate.payload === "object" &&
    candidate.payload !== null
  );
}

export function useInvestigationStream(investigationId: string | undefined, enabled = true): StreamState {
  const [state, setState] = useState<StreamState>({
    events: [], status: "connecting", lastSeq: 0, terminal: null, error: null,
  });
  const seenRef = useRef<Set<number>>(new Set());
  const sourceRef = useRef<EventSource | null>(null);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  useEffect(() => {
    if (!investigationId || !enabled) return;
    seenRef.current = new Set();
    setState({ events: [], status: "connecting", lastSeq: 0, terminal: null, error: null });

    const source = new EventSource(eventStreamUrl(investigationId));
    sourceRef.current = source;

    const handle = (raw: MessageEvent<string>) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw.data);
      } catch {
        return; // a malformed frame is dropped rather than corrupting the timeline
      }
      if (!isInvestigationEvent(parsed)) return;
      const event = parsed;
      if (seenRef.current.has(event.seq)) return; // replayed overlap after a reconnect
      seenRef.current.add(event.seq);

      setState((previous) => {
        const events = [...previous.events, event].sort((a, b) => a.seq - b.seq);
        const terminal = TERMINAL_EVENTS.includes(event.type) ? event : previous.terminal;
        return {
          events,
          status: terminal ? "closed" : "open",
          lastSeq: Math.max(previous.lastSeq, event.seq),
          terminal,
          error: null,
        };
      });

      if (TERMINAL_EVENTS.includes(event.type)) {
        source.close();
        sourceRef.current = null;
      }
    };

    for (const type of EVENT_TYPES) source.addEventListener(type, handle as EventListener);
    source.addEventListener("open", () => {
      setState((previous) => (previous.terminal ? previous : { ...previous, status: "open", error: null }));
    });
    source.addEventListener("error", () => {
      // EventSource reconnects on its own and resends Last-Event-ID; reflect that rather than hiding it.
      setState((previous) => {
        if (previous.terminal) return previous;
        const closed = source.readyState === EventSource.CLOSED;
        return {
          ...previous,
          status: closed ? "error" : "reconnecting",
          error: closed ? "The event stream closed unexpectedly." : null,
        };
      });
    });

    return () => {
      for (const type of EVENT_TYPES) source.removeEventListener(type, handle as EventListener);
      source.close();
      sourceRef.current = null;
    };
  }, [investigationId, enabled]);

  useEffect(() => close, [close]);
  return state;
}

/** Narrow an event to one type, for components that only care about a slice of the stream. */
export function eventsOfType<T extends EventType>(
  events: InvestigationEvent[],
  type: T,
): Extract<InvestigationEvent, { type: T }>[] {
  return events.filter((event): event is Extract<InvestigationEvent, { type: T }> => event.type === type);
}
