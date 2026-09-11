/** The screens, rendered against fixtures captured from the real API. */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EvaluationsPage } from "@/pages/EvaluationsPage";
import { IncidentDetailPage } from "@/pages/IncidentDetailPage";
import { IncidentsPage } from "@/pages/IncidentsPage";
import { McpPage } from "@/pages/McpPage";
import { SystemPage } from "@/pages/SystemPage";
import { ReportView } from "@/components/ReportView";
import { SystemProvider } from "@/hooks/useSystem";
import { fixtures, renderRoute, stubApi } from "./helpers";
import type { InvestigationReport } from "@/types/api";

const withSystem = (element: React.ReactElement) => <SystemProvider>{element}</SystemProvider>;

describe("IncidentsPage", () => {
  it("lists every open incident with severity and service", async () => {
    stubApi();
    renderRoute(<IncidentsPage />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(16); // header plus 15 incidents
    expect(screen.getByText("INC-2026-0101")).toBeInTheDocument();
    expect(screen.getAllByText("checkout").length).toBeGreaterThan(0);
  });

  it("filters by search text", async () => {
    stubApi();
    const user = userEvent.setup();
    renderRoute(<IncidentsPage />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

    await user.type(screen.getByLabelText("Search incidents"), "INC-2026-0101");
    await waitFor(() => {
      expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
    });
    expect(screen.getByText("INC-2026-0101")).toBeInTheDocument();
  });

  it("shows an actionable message when the API is offline", async () => {
    stubApi({ overrides: [{ match: /\/api\/incidents$/, throws: true }] });
    renderRoute(<IncidentsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/not reachable/i)).toBeInTheDocument();
    expect(screen.getAllByText(/signalforge serve/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("never shows scenario ground truth", async () => {
    stubApi();
    const { container } = renderRoute(<IncidentsPage />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const text = container.textContent!.toLowerCase();
    for (const forbidden of ["scn-", "root cause", "deployment_regression", "red herring", "decisive"]) {
      expect(text).not.toContain(forbidden);
    }
  });
});

describe("IncidentDetailPage", () => {
  it("offers the scripted provider and states that no live API is used", async () => {
    stubApi();
    renderRoute(withSystem(<IncidentDetailPage />), { path: "/incidents/:incidentId", route: "/incidents/INC-2026-0101" });
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /^Investigate$/ })).toBeEnabled();
    expect(screen.getByText("USES LIVE API: NO")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Scripted demonstration/ })).toBeChecked();
  });

  it("disables providers the server has not configured, and never asks for a key", async () => {
    stubApi();
    renderRoute(withSystem(<IncidentDetailPage />), { path: "/incidents/:incidentId", route: "/incidents/INC-2026-0101" });
    await waitFor(() => expect(screen.getByRole("radio", { name: /Anthropic/ })).toBeInTheDocument());

    expect(screen.getByRole("radio", { name: /Anthropic/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /OpenAI/ })).toBeDisabled();
    expect(screen.getAllByText(/missing: ANTHROPIC_API_KEY/).length).toBeGreaterThan(0);

    // No input anywhere could accept a credential.
    for (const input of screen.queryAllByRole("textbox")) {
      expect(input).not.toHaveAttribute("type", "password");
    }
    expect(screen.queryByLabelText(/api key/i)).toBeNull();
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });

  it("creates an investigation with the chosen budget and navigates to it", async () => {
    const { fetchMock } = stubApi({
      overrides: [{ match: /\/api\/investigations$/, status: 202, body: fixtures.investigation }],
    });
    const user = userEvent.setup();
    renderRoute(withSystem(<IncidentDetailPage />), { path: "/incidents/:incidentId", route: "/incidents/INC-2026-0101" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Investigate$/ })).toBeEnabled());

    await user.click(screen.getByRole("radio", { name: /Thorough/ }));
    await user.click(screen.getByRole("button", { name: /^Investigate$/ }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeDefined();
      expect(JSON.parse(String(post![1]!.body))).toEqual({
        incident_id: "INC-2026-0101", provider: "scripted", budget_profile: "thorough",
      });
    });
  });

  it("surfaces a runner-busy refusal without losing the page", async () => {
    stubApi({
      overrides: [{
        match: /\/api\/investigations$/, status: 429,
        body: { error: { code: "too_many_investigations", message: "runner full", request_id: "r1" } },
      }],
    });
    const user = userEvent.setup();
    renderRoute(withSystem(<IncidentDetailPage />), { path: "/incidents/:incidentId", route: "/incidents/INC-2026-0101" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Investigate$/ })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: /^Investigate$/ }));
    await waitFor(() => expect(screen.getByText(/runner is busy/i)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /^Investigate$/ })).toBeEnabled();
  });
});

describe("McpPage", () => {
  it("shows the real catalogue and offers no way to execute a tool", async () => {
    stubApi();
    renderRoute(<McpPage />);
    await waitFor(() => expect(screen.getByText("signalforge-ops")).toBeInTheDocument());

    expect(screen.getByText("2026-07-28")).toBeInTheDocument();
    expect(screen.getByText(/Tools \(9\)/)).toBeInTheDocument();
    expect(screen.getByText("query_metrics")).toBeInTheDocument();
    expect(screen.getAllByText("read-only").length).toBe(9);

    // Catalogue only: nothing on this page runs anything.
    for (const button of screen.queryAllByRole("button")) {
      expect(button.textContent?.toLowerCase()).not.toMatch(/execute|run|call|invoke/);
    }
    expect(document.querySelector("form")).toBeNull();
  });

  it("lists resources and templates", async () => {
    stubApi();
    renderRoute(<McpPage />);
    await waitFor(() => expect(screen.getByText("incidents://open")).toBeInTheDocument());
    expect(screen.getByText("runbook://{runbook_id}")).toBeInTheDocument();
    expect(screen.getAllByText("template").length).toBeGreaterThan(0);
  });
});

describe("EvaluationsPage", () => {
  it("renders headline metrics from the API, not from hardcoded numbers", async () => {
    stubApi();
    renderRoute(<EvaluationsPage />);
    await waitFor(() => expect(screen.getByText("Scenarios passed")).toBeInTheDocument());

    const aggregates = fixtures.benchmark.aggregates as Record<string, number>;
    const metric = (label: string) => within(screen.getByText(label).closest(".panel") as HTMLElement);

    expect(metric("Scenarios passed").getByText(`${aggregates.passed} / ${aggregates.scenarios}`)).toBeInTheDocument();
    expect(metric("Citation validity").getByText("100%")).toBeInTheDocument();
    expect(metric("Unsupported claims").getByText("0%")).toBeInTheDocument();
    expect(metric("MCP tool calls").getByText(String(aggregates.tool_calls_total))).toBeInTheDocument();
    expect(metric("Repair rounds").getByText(String(aggregates.repair_rounds_total))).toBeInTheDocument();
    expect(metric("Decisive evidence recall").getByText(`${(aggregates.decisive_evidence_recall_mean! * 100).toFixed(1)}%`))
      .toBeInTheDocument();
  });

  it("explains that the inconclusive scenario is correct behaviour", async () => {
    stubApi();
    renderRoute(<EvaluationsPage />);
    await waitFor(() => expect(screen.getByText(/scenario with no answer/i)).toBeInTheDocument());
    const panel = within(screen.getByText(/scenario with no answer/i).closest(".panel") as HTMLElement);
    expect(panel.getAllByText(/inconclusive/).length).toBeGreaterThan(0);
    expect(panel.getByText(/confident cause here/i)).toBeInTheDocument();
    expect(panel.getByText("SCN-15")).toBeInTheDocument();
    expect(panel.getByText("calibrated")).toBeInTheDocument();
  });

  it("lists every scenario with its result", async () => {
    stubApi();
    renderRoute(<EvaluationsPage />);
    await waitFor(() => expect(screen.getByText(/Scenarios \(15\)/)).toBeInTheDocument());
    expect(screen.getAllByText("PASS")).toHaveLength(15);
    expect(screen.getByText("SCN-01")).toBeInTheDocument();
  });
});

describe("SystemPage", () => {
  it("reports configuration without describing any credential", async () => {
    stubApi();
    renderRoute(withSystem(<SystemPage />));
    await waitFor(() => expect(screen.getByText("MCP protocol")).toBeInTheDocument());

    expect(screen.getByText("SignalForge Demo Commerce")).toBeInTheDocument();
    expect(screen.getAllByText("DISABLED").length).toBeGreaterThan(0);
    expect(screen.getByText(/never reach this page/i)).toBeInTheDocument();

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/sk-[A-Za-z0-9_-]{8,}/);
    expect(text).not.toMatch(/Bearer\s+\S+/);
  });
});

describe("ReportView", () => {
  const report = fixtures.report as unknown as InvestigationReport;

  it("shows the primary hypothesis, confidence and grounding status", () => {
    renderRoute(<ReportView report={report} />);
    expect(screen.getByText("PRIMARY HYPOTHESIS")).toBeInTheDocument();
    expect(screen.getByText(report.primary_hypothesis!.statement)).toBeInTheDocument();
    expect(screen.getByText("GROUNDING VALIDATED")).toBeInTheDocument();
    expect(screen.getByText(report.summary)).toBeInTheDocument();
  });

  it("separates observed, inferred and unknown claims", () => {
    renderRoute(<ReportView report={report} />);
    expect(screen.getByText("Observed")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    for (const claim of report.key_findings) {
      expect(screen.getByText(claim.statement)).toBeInTheDocument();
    }
  });

  it("makes every citation clickable and resolves narrowed ones to their evidence item", async () => {
    const selected: string[] = [];
    const user = userEvent.setup();
    renderRoute(<ReportView report={report} onSelectEvidence={(id) => selected.push(id)} />);

    const citation = report.primary_hypothesis!.supporting_evidence_ids[0]!;
    await user.click(screen.getAllByRole("button", { name: citation })[0]!);
    // EVD-000004#DEP-0038 opens EVD-000004.
    expect(selected[0]).toBe(citation.split("#")[0]);
    expect(selected[0]).toMatch(/^EVD-\d{6}$/);
  });

  it("reports token usage honestly for a provider that uses no model", () => {
    renderRoute(<ReportView report={report} />);
    expect(screen.getByText(/tokens not reported/i)).toBeInTheDocument();
  });
});
