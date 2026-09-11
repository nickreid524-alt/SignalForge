/**
 * The investigation workspace, driven by the real event fixture.
 *
 * This is the closest thing to an end-to-end test that runs in the unit suite: it plays the exact
 * event sequence the API emitted for a real scripted investigation, and checks that the timeline,
 * the hypothesis board and the evidence workbench all build from it.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { InvestigationPage } from "@/pages/InvestigationPage";
import { Timeline } from "@/components/Timeline";
import { TraceView } from "@/components/TraceView";
import { FakeEventSource, fixtures, renderRoute, stubApi, stubEventSource } from "./helpers";
import type { InvestigationEvent, InvestigationTrace } from "@/types/api";

const EVENTS = fixtures.events;
const ROUTE = { path: "/investigations/:investigationId", route: `/investigations/${fixtures.investigation.id}` };

function renderWorkspace() {
  return renderRoute(<InvestigationPage />, ROUTE);
}

async function playAll(source: FakeEventSource) {
  await act(async () => {
    for (const event of EVENTS) source.emit(event);
  });
}

/**
 * Play everything up to but not including the terminal event. The workspace deliberately moves to
 * the report once an investigation completes, so tests about the live timeline stop just short.
 */
async function playWhileRunning(source: FakeEventSource) {
  await act(async () => {
    for (const event of EVENTS.slice(0, -1)) source.emit(event);
  });
}

describe("InvestigationPage", () => {
  beforeEach(() => {
    stubEventSource();
  });

  it("subscribes to the stream for the investigation in the route", async () => {
    stubApi();
    renderWorkspace();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.latest().url).toContain(`/api/investigations/${fixtures.investigation.id}/events`);
  });

  it("shows a waiting state before the first event", async () => {
    stubApi();
    renderWorkspace();
    expect(await screen.findByText(/waiting for the first event/i)).toBeInTheDocument();
  });

  it("builds the timeline, hypothesis board and evidence list from the stream", async () => {
    stubApi();
    renderWorkspace();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await playWhileRunning(FakeEventSource.latest());

    // MCP tool activity is visible as such.
    await waitFor(() => expect(screen.getAllByText("MCP TOOL").length).toBeGreaterThan(0));
    expect(screen.getAllByText("PROVIDER STEP").length).toBeGreaterThan(0);
    expect(screen.getAllByText("RESOURCE READ").length).toBeGreaterThan(0);
    expect(screen.getAllByText("HYPOTHESIS").length).toBeGreaterThan(0);

    // Hypotheses evolve.
    const board = screen.getByText(/^Hypotheses \(/).closest(".panel") as HTMLElement;
    expect(within(board).getByText("H1")).toBeInTheDocument();
    expect(within(board).getAllByText("evolution").length).toBeGreaterThan(0);

    // Evidence accumulates and is labelled untrusted in the timeline.
    expect(screen.getAllByText("UNTRUSTED").length).toBeGreaterThan(0);
  });

  it("renders one entry per event, in order", async () => {
    stubApi();
    renderWorkspace();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await playWhileRunning(FakeEventSource.latest());

    await waitFor(() => expect(screen.getByText(`${EVENTS.length - 1} events`)).toBeInTheDocument());
    const marks = screen.getAllByText(/^#\d+ · /).map((node) => Number(node.textContent!.match(/#(\d+)/)![1]));
    expect(marks).toEqual([...marks].sort((a, b) => a - b));
    expect(marks[0]).toBe(1);
  });

  it("switches to the report when the investigation completes, with working citations", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWorkspace();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await playAll(FakeEventSource.latest());

    // The terminal event moves the workspace to the conclusion.
    await waitFor(() => expect(screen.getByText("PRIMARY HYPOTHESIS")).toBeInTheDocument());
    expect(screen.getByText("GROUNDING VALIDATED")).toBeInTheDocument();

    const citation = fixtures.report.primary_hypothesis!.supporting_evidence_ids[0]!;
    await user.click(screen.getAllByRole("button", { name: citation })[0]!);

    // Clicking a citation opens that evidence item, with its untrusted label.
    const base = citation.split("#")[0]!;
    await waitFor(() => expect(screen.getByText(`Evidence ${base}`)).toBeInTheDocument());
    expect(screen.getByText(/Untrusted evidence/i)).toBeInTheDocument();
  });

  it("reports a failed investigation without pretending it has a report", async () => {
    stubApi({
      overrides: [
        { match: /\/api\/investigations\/[^/]+$/, body: { ...fixtures.investigation, status: "failed", report_available: false, error: "provider failed [authentication]" } },
        { match: /\/report$/, status: 409, body: { error: { code: "report_not_ready", message: "none", request_id: "r" } } },
      ],
    });
    renderWorkspace();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    const failure: InvestigationEvent = {
      seq: 1, investigation_id: fixtures.investigation.id, type: "investigation.failed",
      at: new Date().toISOString(),
      payload: { terminal_status: "failed", message: "invalid credential", category: "authentication", duration_seconds: 0.1 },
    };
    await act(async () => FakeEventSource.latest().emit(failure));

    await waitFor(() => expect(screen.getByText("Investigation failed")).toBeInTheDocument());
    expect(screen.getByText("invalid credential")).toBeInTheDocument();
    expect(screen.getAllByText("authentication").length).toBeGreaterThan(0);
  });

  it("warns when the stream is lost and keeps polling", async () => {
    stubApi();
    renderWorkspace();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => FakeEventSource.latest().emit(EVENTS[0]!));
    await act(async () => FakeEventSource.latest().emitError({ fatal: true }));

    await waitFor(() => expect(screen.getByText(/live event stream dropped/i)).toBeInTheDocument());
    expect(screen.getByText(/no events are lost/i)).toBeInTheDocument();
  });
});

describe("Timeline", () => {
  it("shows an MCP tool call as a command with its arguments and evidence id", () => {
    const requested = EVENTS.find((e) => e.type === "tool.requested")!;
    const completed = EVENTS.find((e) => e.type === "tool.completed")!;
    renderRoute(<Timeline events={[requested, completed]} />);

    const name = (requested.payload as { name: string }).name;
    expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    for (const [key, value] of Object.entries((requested.payload as { arguments: Record<string, unknown> }).arguments)) {
      expect(screen.getByText(key)).toBeInTheDocument();
      expect(screen.getByText(typeof value === "string" ? value : JSON.stringify(value))).toBeInTheDocument();
    }
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("marks a refused action distinctly", () => {
    const rejected: InvestigationEvent = {
      seq: 9, investigation_id: "inv-x", type: "tool.rejected", at: new Date().toISOString(),
      payload: { step: 2, request_id: "t2", name: "disable_fraud_checks", code: "unknown_action", reason: "not an allowed tool", duplicate_of: null },
    };
    renderRoute(<Timeline events={[rejected]} />);
    expect(screen.getByText("REFUSED")).toBeInTheDocument();
    expect(screen.getByText("disable_fraud_checks")).toBeInTheDocument();
    expect(screen.getByText("unknown action")).toBeInTheDocument();
    expect(screen.getByText("not an allowed tool")).toBeInTheDocument();
  });

  it("renders evidence text as text, never as markup", () => {
    const hostile: InvestigationEvent = {
      seq: 3, investigation_id: "inv-x", type: "hypothesis.updated", at: new Date().toISOString(),
      payload: {
        hypothesis_id: "H1", step: 1, statement: "<img src=x onerror=alert(1)>malicious</img>",
        status: "proposed", confidence: 0.5, supporting_evidence_ids: [], contradicting_evidence_ids: [], note: "",
      },
    };
    const { container } = renderRoute(<Timeline events={[hostile]} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>malicious</img>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("TraceView", () => {
  it("is labelled an observable trace and exposes no reasoning", () => {
    const { container } = renderRoute(<TraceView trace={fixtures.trace as unknown as InvestigationTrace} />);
    expect(screen.getByText("Observable investigation trace")).toBeInTheDocument();
    const text = container.textContent!.toLowerCase();
    expect(text).not.toContain("chain of thought");
    for (const forbidden of ["opaque", "encrypted_content", "redacted_thinking"]) {
      expect(text).not.toContain(forbidden);
    }
    expect(screen.getByText(/State transitions/)).toBeInTheDocument();
    expect(screen.getByText(/Provider calls/)).toBeInTheDocument();
  });
});
