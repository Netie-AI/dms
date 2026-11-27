import os from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/*
 * Service-worker check against a PRODUCTION build (public/sw.js registers only in
 * production). Separate from playwright.config.ts: it needs no API stack.
 * SW_DIST serves an already built directory (used to run the spec against a mutated
 * copy of sw.js); unset, the build goes to a temp directory outside the repo.
 */
// Lets context.route see the requests the service worker itself makes.
process.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS = "1";

const port = Number(process.env.SW_PORT ?? 4791);
const dist = process.env.SW_DIST ?? path.join(os.tmpdir(), "dms-ui-sw-dist");
const preview = `npx vite preview --outDir "${dist}" --host 127.0.0.1 --port ${port} --strictPort`;

export default defineConfig({
  testDir: "./e2e-sw",
  // Not *.spec.ts: vitest would collect that and fail on Playwright imports.
  testMatch: "**/*.pw.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${port}`,
    serviceWorkers: "allow",
    trace: "retain-on-failure",
    ...(process.env.PW_CHROMIUM
      ? { launchOptions: { executablePath: process.env.PW_CHROMIUM } }
      : {}),
  },
  webServer: {
    command: process.env.SW_DIST
      ? preview
      : `npx vite build --outDir "${dist}" --emptyOutDir && ${preview}`,
    url: `http://localhost:${port}/`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  timeout: 30_000,
  expect: { timeout: 10_000 },
});
