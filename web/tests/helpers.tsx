/**
 * Test helpers.
 *
 * The fixtures in `tests/fixtures` are real responses captured from the SignalForge API, not
 * invented shapes, so a change to the backend contract shows up as a failing test rather than a
 * silently wrong screen. Mocking stops at the network boundary: every component under test runs the
 * same client code the application runs.
 */

import type { ReactElement } from "react";
import { render, type RenderResult } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { InvestigationEvent } from "@/types/api";

import benchmark from "./fixtures/benchmark.json";
import evaluations from "./fixtures/evaluations.json";
import events from "./fixtures/events.json";
import evidence from "./fixtures/evidence.json";
import evidenceItem from "./fixtures/evidence_item.json";
import health from "./fixtures/health.json";
import hypotheses from "./fixtures/hypotheses.json";
import incident from "./fixtures/incident.json";
import incidents from "./fixtures/incidents.json";
import investigation from "./fixtures/investigation.json";
import investigations from "./fixtures/investigations.json";
import mcpResources from "./fixtures/mcp_resources.json";
import mcpTools from "./fixtures/mcp_tools.json";
import meta from "./fixtures/meta.json";
import providers from "./fixtures/providers.json";
import report from "./fixtures/report.json";
import trace from "./fixtures/trace.json";

export const fixtures = {
  benchmark, evaluations, events: events as unknown as InvestigationEvent[], evidence, evidenceItem,
  health, hypotheses, incident, incidents, investigation, investigations, mcpResources, mcpTools,
  meta, providers, report, trace,
};

export const INVESTIGATION_ID = investigation.id;

/** Route table mapping API paths to fixtures, used by the default fetch stub. */
function defaultRoutes(): { match: RegExp; body: unknown }[] {
  return [
    { match: /\/api\/health$/, body: health },
    { match: /\/api\/meta$/, body: meta },
    { match: /\/api\/providers$/, body: providers },
    { match: /\/api\/incidents$/, body: incidents },
    { match: /\/api\/incidents\/[^/]+$/, body: incident },
    { match: /\/api\/mcp\/tools$/, body: mcpTools },
    { match: /\/api\/mcp\/resources$/, body: mcpResources },
    { match: /\/api\/investigations$/, body: investigations },
    { match: /\/api\/investigations\/[^/]+\/hypotheses$/, body: hypotheses },
    { match: /\/api\/investigations\/[^/]+\/evidence\/[^/]+$/, body: evidenceItem },
    { match: /\/api\/investigations\/[^/]+\/evidence$/, body: evidence },
    { match: /\/api\/investigations\/[^/]+\/report$/, body: report },
    { match: /\/api\/investigations\/[^/]+\/trace$/, body: trace },
    { match: /\/api\/evaluations\/[^/]+$/, body: benchmark },
    { match: /\/api\/evaluations$/, body: evaluations },
    { match: /\/api\/investigations\/[^/]+$/, body: investigation },
  ];
}

export interface StubOptions {
  /** Override or add responses: path regex source -> body, status or thrown error. */
  overrides?: { match: RegExp; body?: unknown; status?: number; throws?: boolean }[];
}

export function stubApi(options: StubOptions = {}) {
  const routes = defaultRoutes();
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init;
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push(url);
    const path = url.split("?")[0] ?? url;

    for (const override of options.overrides ?? []) {
      if (!override.match.test(path)) continue;
      if (override.throws) throw new TypeError("Failed to fetch");
      const status = override.status ?? 200;
      return new Response(JSON.stringify(override.body ?? {}), {
        status, headers: { "content-type": "application/json" },
      });
    }
    for (const route of routes) {
      if (route.match.test(path)) {
        return new Response(JSON.stringify(route.body), { status: 200, headers: { "content-type": "application/json" } });
      }
    }
    return new Response(
      JSON.stringify({ error: { code: "not_found", message: `no stub for ${path}`, request_id: "test" } }),
      { status: 404, headers: { "content-type": "application/json" } },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

/** Render at a route, with the router wired the way the application wires it. */
export function renderRoute(element: ReactElement, { path = "/", route = "/" }: { path?: string; route?: string } = {}): RenderResult {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path={path} element={element} />
        <Route path="*" element={element} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------- EventSource double

type Listener = (event: MessageEvent<string>) => void;

/**
 * A controllable EventSource. jsdom has none, and the real reconnection behaviour is what we want to
 * test: the class records the `Last-Event-ID` the application would send on reconnect.
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  lastEventId = "";
  closed = false;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }

  /** Deliver one event exactly as the server frames it. */
  emit(event: InvestigationEvent) {
    this.readyState = FakeEventSource.OPEN;
    this.lastEventId = String(event.seq);
    const message = new MessageEvent(event.type, { data: JSON.stringify(event), lastEventId: String(event.seq) });
    for (const listener of this.listeners.get(event.type) ?? []) listener(message as MessageEvent<string>);
  }

  emitOpen() {
    this.readyState = FakeEventSource.OPEN;
    for (const listener of this.listeners.get("open") ?? []) listener(new MessageEvent("open") as MessageEvent<string>);
  }

  /** Simulate a dropped connection. `fatal` closes for good; otherwise the browser would retry. */
  emitError({ fatal = false } = {}) {
    this.readyState = fatal ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    for (const listener of this.listeners.get("error") ?? []) listener(new MessageEvent("error") as MessageEvent<string>);
  }

  static latest(): FakeEventSource {
    const instance = FakeEventSource.instances.at(-1);
    if (!instance) throw new Error("no EventSource was created");
    return instance;
  }

  static reset() {
    FakeEventSource.instances = [];
  }
}

export function stubEventSource() {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  return FakeEventSource;
}
