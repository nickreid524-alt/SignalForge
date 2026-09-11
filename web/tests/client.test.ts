/** The API client: parsing, error normalisation, and the request shapes it is allowed to send. */

import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createInvestigation,
  getBenchmark,
  getIncidents,
  getInvestigation,
  getMcpTools,
  getReport,
} from "@/api/client";
import { fixtures, stubApi } from "./helpers";

describe("API client", () => {
  it("parses the incident queue", async () => {
    stubApi();
    const incidents = await getIncidents();
    expect(incidents).toHaveLength(15);
    expect(incidents[0]).toMatchObject({ id: expect.stringMatching(/^INC-\d{4}-\d{4}$/) });
    // The client returns exactly the investigator-visible fields; no cause information exists to leak.
    expect(Object.keys(incidents[0]!).sort()).toEqual([
      "affected_service", "detected_at", "id", "investigation_clock", "reporter", "severity", "title",
    ]);
  });

  it("parses an investigation detail, including honest token reporting", async () => {
    stubApi();
    const detail = await getInvestigation(fixtures.investigation.id);
    expect(detail.status).toBe("completed");
    expect(detail.token_usage.reported).toBe(false);
    expect(detail.token_usage.input_tokens).toBeNull();
    expect(detail.links.events).toContain("/events");
  });

  it("parses the MCP catalogue with schemas and read-only hints", async () => {
    stubApi();
    const catalogue = await getMcpTools();
    expect(catalogue.tools).toHaveLength(9);
    expect(catalogue.read_only).toBe(true);
    expect(catalogue.tools.every((tool) => tool.read_only)).toBe(true);
    expect(catalogue.tools[0]!.input_schema.type).toBe("object");
  });

  it("parses the frozen benchmark", async () => {
    stubApi();
    const benchmark = await getBenchmark("scripted");
    expect(benchmark.scenario_count).toBe(15);
    expect(benchmark.uses_live_api).toBe(false);
    expect(benchmark.aggregates.pass_rate).toBe(1);
  });

  it("sends only an incident, a provider and a budget profile", async () => {
    const { fetchMock } = stubApi({
      overrides: [{ match: /\/api\/investigations$/, body: fixtures.investigation, status: 202 }],
    });
    await createInvestigation({ incident_id: "INC-2026-0101", provider: "scripted", budget_profile: "quick" });
    const init = fetchMock.mock.calls[0]![1]!;
    const body = JSON.parse(String(init.body));
    expect(Object.keys(body).sort()).toEqual(["budget_profile", "incident_id", "provider"]);
    // Nothing resembling a credential, a model, a prompt or a path is ever sent.
    for (const forbidden of ["api_key", "model", "system_prompt", "base_url", "cassette", "trace_db"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
    expect(init.method).toBe("POST");
  });

  describe("error handling", () => {
    it("normalises a 409 report_not_ready", async () => {
      stubApi({
        overrides: [{
          match: /\/report$/,
          status: 409,
          body: { error: { code: "report_not_ready", message: "no report yet", request_id: "abc123" } },
        }],
      });
      const error = await getReport("inv-x").catch((cause: unknown) => cause);
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: "report_not_ready", status: 409, requestId: "abc123" });
    });

    it("normalises provider_unavailable and too_many_investigations", async () => {
      for (const [code, status] of [["provider_unavailable", 409], ["too_many_investigations", 429]] as const) {
        stubApi({
          overrides: [{ match: /\/api\/investigations$/, status, body: { error: { code, message: code, request_id: "r" } } }],
        });
        const error = await createInvestigation({ incident_id: "INC-2026-0101" }).catch((cause: unknown) => cause);
        expect(error).toMatchObject({ code, status });
      }
    });

    it("turns an unreachable API into an offline error rather than a crash", async () => {
      stubApi({ overrides: [{ match: /\/api\/incidents$/, throws: true }] });
      const error = await getIncidents().catch((cause: unknown) => cause);
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).isOffline).toBe(true);
      expect((error as ApiError).message).toContain("signalforge serve");
    });

    it("survives an error body that is not JSON", async () => {
      vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>502</html>", { status: 502 })));
      const error = await getIncidents().catch((cause: unknown) => cause);
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(502);
      expect((error as ApiError).code).toBe("internal_error");
    });
  });
});
