import { defineConfig, devices } from "@playwright/test";

const ci = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: ci,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  ...(ci
    ? {
        webServer: {
          command: "npm run dev -- --host 127.0.0.1 --port 3000 --strictPort",
          url: "http://127.0.0.1:3000",
          reuseExistingServer: false,
          timeout: 120_000,
        },
      }
    : {}),
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  timeout: 30_000,
  expect: { timeout: 10_000 },
});
