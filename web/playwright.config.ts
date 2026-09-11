/**
 * Two focused end-to-end flows against the real stack: the SignalForge API, the real MCP server and
 * the built frontend. Deliberately small. The unit suite covers breadth; this proves the whole thing
 * works together over HTTP and SSE.
 *
 * The scripted provider is the only one used, so no vendor API is contacted and nothing costs money.
 */

import { defineConfig, devices } from "@playwright/test";

const API_PORT = Number(process.env.SIGNALFORGE_E2E_API_PORT ?? 8799);
const WEB_PORT = Number(process.env.SIGNALFORGE_E2E_WEB_PORT ?? 5199);
const PYTHON = process.env.SIGNALFORGE_PYTHON ?? "python";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      // In-memory stores: an end-to-end run leaves nothing behind.
      command: `${PYTHON} -m signalforge.cli serve --port ${API_PORT} --trace-db :memory: --event-db :memory:`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      cwd: "..",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { SIGNALFORGE_API: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
