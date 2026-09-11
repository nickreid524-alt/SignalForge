/**
 * Capture the portfolio screenshots from the real application.
 *
 * Run deliberately with `npm run screenshots`; it is not part of the CI end-to-end run. Everything
 * shown is produced by the real API, the real MCP server and the scripted demonstration provider, so
 * the images cannot show anything the application does not actually do.
 *
 * Scenario: SCN-05 / INC-2026-0105, chosen by measuring every scenario's composition. It gives ten
 * MCP tool calls, thirteen evidence items, three hypotheses of which two are refuted, every
 * hypothesis revised at least once, and a clearly cited root cause.
 */

import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(process.cwd(), "..", "docs", "screenshots");
const INCIDENT = "INC-2026-0105";

test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true });
});

test("capture the portfolio screenshot set", async ({ page }) => {
  test.setTimeout(180_000);

  // ---------------------------------------------------------------- incident queue
  await page.goto("/incidents");
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(16);
  await settle(page);
  await page.screenshot({ path: `${OUT}/incident-queue.png` });

  // ---------------------------------------------------------------- run an investigation
  await page.goto(`/incidents/${INCIDENT}`);
  await page.getByRole("button", { name: "Investigate", exact: true }).click();
  await expect(page).toHaveURL(/\/investigations\/inv-inc-2026-0105-/);

  // Wait for the investigation to finish, then return to the timeline for the hero.
  await expect(page.getByText("completed").first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("PRIMARY HYPOTHESIS", { exact: true })).toBeVisible();

  // ---------------------------------------------------------------- hero: the live timeline
  await page.getByRole("tab", { name: "timeline" }).click();
  await expect(page.getByText("MCP TOOL").first()).toBeVisible();
  // Scroll the timeline so the frame opens on MCP activity rather than the seeding preamble.
  await page.evaluate(() => {
    const scroller = Array.from(document.querySelectorAll("div")).find(
      (node) => node.scrollHeight > node.clientHeight + 40 && node.querySelector(".tl") !== null,
    );
    if (scroller) scroller.scrollTop = 210;
  });
  await settle(page);
  await page.screenshot({ path: `${OUT}/investigation-workspace.png` });

  // ---------------------------------------------------------------- grounded report
  await page.getByRole("tab", { name: "report" }).click();
  await expect(page.getByText("GROUNDING VALIDATED", { exact: true })).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/grounded-report.png` });

  // ---------------------------------------------------------------- MCP catalogue
  await page.goto("/mcp");
  await expect(page.getByText("signalforge-ops").first()).toBeVisible();
  await expect(page.getByText(/Tools \(9\)/)).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/mcp-catalogue.png` });

  // ---------------------------------------------------------------- evaluation dashboard
  await page.goto("/evaluations");
  await expect(page.getByText("Scenarios passed", { exact: true })).toBeVisible();
  await expect(page.getByText(/scenario with no answer/i)).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/evaluations.png` });
});

/** Let fonts, layout and any transition finish, and park the cursor away from the content. */
async function settle(page: import("@playwright/test").Page) {
  await page.mouse.move(1430, 890);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
}
