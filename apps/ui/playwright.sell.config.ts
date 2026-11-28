import { defineConfig, devices } from "@playwright/test";

const port = process.env.DMS_SELL_DEMO_PORT ?? "8091";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "sell-profile.spec.ts",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3011",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${process.env.SELL_PYTHON || "python"} ../../scripts/verify/sell_profile_demo_server.py`,
      url: `http://127.0.0.1:${port}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { ...process.env, DMS_SELL_PROFILE: "1", DMS_SELL_DEMO_PORT: port },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 3011 --strictPort",
      url: "http://127.0.0.1:3011",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        VITE_API_TARGET: `http://127.0.0.1:${port}`,
      },
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  timeout: 60_000,
  expect: { timeout: 20_000 },
});
