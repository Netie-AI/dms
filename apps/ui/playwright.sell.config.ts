import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const ui = path.dirname(fileURLToPath(import.meta.url));
const ci = !!process.env.CI;

/** Sell walk lives under tests/e2e so CI runs the same click-through on every PR. */
export default defineConfig({
  testDir: path.resolve(ui, "../../tests/e2e"),
  testMatch: "studio_sell_walk.spec.ts",
  fullyParallel: false,
  forbidOnly: ci,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 3000 --strictPort",
    cwd: ui,
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !ci,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  timeout: 60_000,
  expect: { timeout: 10_000 },
});
