/**
 * The flow a reviewer would follow: open an incident, start a scripted investigation, watch it run,
 * and check that the conclusion is traceable back to the evidence it cites.
 *
 * Nothing is stubbed. The events come from the real SSE endpoint, the evidence from the real MCP
 * server, and the report from the real grounding validator.
 */

import { expect, test } from "@playwright/test";

test("a scripted investigation runs end to end and its conclusion is traceable", async ({ page }) => {
  await page.goto("/incidents");

  // The environment identifies itself as synthetic before anything else.
  await expect(page.getByText("SYNTHETIC OPERATIONS ENVIRONMENT", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(16); // header plus 15 incidents

  await page.getByRole("link", { name: "INC-2026-0101" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Checkout latency");

  // The launch control states plainly that no live API is involved.
  await expect(page.getByText("USES LIVE API: NO", { exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Anthropic/ })).toBeDisabled();
  await page.getByRole("button", { name: "Investigate", exact: true }).click();

  // The workspace opens on the live timeline.
  await expect(page).toHaveURL(/\/investigations\/inv-inc-2026-0101-/);
  await expect(page.getByText("MCP TOOL").first()).toBeVisible();
  await expect(page.getByText("PROVIDER", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("RESOURCE", { exact: true }).first()).toBeVisible();

  // Evidence and hypotheses build up from the stream.
  await expect(page.getByText(/^EVD-000003/).first()).toBeVisible();
  await expect(page.getByText("H1").first()).toBeVisible();

  // The investigation completes and the workspace moves to the conclusion.
  await expect(page.getByText("completed").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("PRIMARY HYPOTHESIS", { exact: true })).toBeVisible();
  await expect(page.getByText("GROUNDING VALIDATED", { exact: true })).toBeVisible();

  // Hypotheses show their evolution, not just a final answer.
  await expect(page.getByText("evolution").first()).toBeVisible();

  // Every claim is traceable: clicking a citation opens exactly that evidence item. A citation's
  // accessible name is its own text, which may be narrowed to one record (EVD-000004#DEP-0038).
  const citation = page.getByRole("button", { name: /^EVD-\d{6}/ }).first();
  const label = (await citation.textContent())!.split("#")[0]!;
  await citation.click();
  await expect(page.getByRole("heading", { name: `Evidence ${label}` })).toBeVisible();
  await expect(page.getByText("Untrusted evidence", { exact: true })).toBeVisible();

  // The observable trace is available and is not labelled as reasoning.
  await page.getByRole("tab", { name: "Observable trace" }).click();
  await expect(page.getByText("Observable investigation trace", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Chain of Thought");
});

test("the evaluation dashboard renders the frozen benchmark from the API", async ({ page }) => {
  await page.goto("/evaluations");

  await expect(page.getByText("Scenarios passed", { exact: true })).toBeVisible();
  await expect(page.getByText("15 / 15", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Citation validity", { exact: true })).toBeVisible();

  // The inconclusive scenario is explained rather than hidden.
  await expect(page.getByText(/scenario with no answer/i)).toBeVisible();
  await expect(page.getByText("SCN-15", { exact: true }).first()).toBeVisible();

  // Every scenario is listed with a result. Scope to the table: "PASS" is also a substring of the
  // "Scenarios passed" metric label above it.
  await expect(page.getByText("Scenarios (15)", { exact: true })).toBeVisible();
  const scenarios = page.getByRole("table").filter({ hasText: "Failure type" });
  await expect(scenarios.getByText("PASS", { exact: true })).toHaveCount(15);
});
