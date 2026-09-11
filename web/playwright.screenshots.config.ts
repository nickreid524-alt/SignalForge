/**
 * Screenshot capture runs against the real stack but is deliberately separate from the CI
 * end-to-end config, so a CI run never writes images and a capture run never gates a merge.
 */

import { defineConfig, devices } from "@playwright/test";

const API_PORT = Number(process.env.SIGNALFORGE_SHOT_API_PORT ?? 8801);
const WEB_PORT = Number(process.env.SIGNALFORGE_SHOT_WEB_PORT ?? 5201);
const PYTHON = process.env.SIGNALFORGE_PYTHON ?? "python";

export default defineConfig({
  testDir: "./screenshots",
  timeout: 240_000,
  workers: 1,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}` },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `${PYTHON} -m signalforge.cli serve --port ${API_PORT} --trace-db :memory: --event-db :memory:`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      cwd: "..",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { SIGNALFORGE_API: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
