// @ts-check
const { defineConfig, devices } = require("@playwright/test");

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const UI = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";

module.exports = defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: UI,
    trace: "on-first-retry",
    extraHTTPHeaders: {
      "X-API-Key": "dms-demo-viewer-key",
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : {
        command: "npm run dev",
        url: UI,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
  metadata: { api: API },
});
