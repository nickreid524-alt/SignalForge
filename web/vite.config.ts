/// <reference types="node" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API is a separate loopback process (`signalforge serve`). In development Vite proxies /api to
// it, so the browser makes same-origin requests and the API needs no CORS origin configured at all.
// For a built bundle served from elsewhere, point VITE_API_BASE at the API instead.
const API_TARGET = process.env.SIGNALFORGE_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // `ws: false` and no buffering: Server-Sent Events must stream through untouched.
      "/api": { target: API_TARGET, changeOrigin: false, ws: false },
    },
  },
  build: { outDir: "dist", sourcemap: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
  },
});
