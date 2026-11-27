import { expect, test } from "@playwright/test";

const FINANCE = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  name: "Finance",
  source_count: 3,
  member_count: 1,
};

const ENVELOPE = {
  answer_id: "ans_e2e",
  text: "no number",
  badge: "ABSTAIN",
  abstained: true,
  assumptions: [],
  values: [],
  rows: [],
  contributing_sources: [],
  as_of: "2026-11-27T00:00:00Z",
};

test.describe("STREAM-STAGES-01 demo Space", () => {
  test("Finance shows ask stages and Cancel aborts the ask", async ({ page }) => {
    let askHeaders: Record<string, string> = {};
    await page.route("**/api/**", async (route) => {
      const url = route.request().url();
      if (url.includes("/v1/chat/ask")) {
        askHeaders = route.request().headers();
        await new Promise((resolve) => setTimeout(resolve, 8000));
        try {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(ENVELOPE),
          });
        } catch {
          // The Cancel button aborts this request.
        }
        return;
      }
      if (url.includes("/v1/studio/ask/") && url.includes("/events")) {
        const body = [
          "retry: 86400000",
          "",
          'data: {"stage":"retrieve","status":"done","t_ms":1200}',
          "",
          'data: {"stage":"generate","status":"started","t_ms":2400}',
          "",
          "",
        ].join("\n");
        await route.fulfill({
          status: 200,
          headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
          body,
        });
        return;
      }
      if (url.includes("/health")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: "ok",
            ask_mode: "live",
            demo_fallback: false,
            database_configured: false,
            backend: "memory",
            dependencies: {
              cortex: { ok: true, contract_routes: true },
              openvault: { ok: true, trust: { jwks_ok: true } },
            },
          }),
        });
        return;
      }
      if (url.includes("/v1/spaces")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ spaces: [FINANCE], persisted: false }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
    });

    await page.goto("/");
    await expect(page.getByText("API · ok")).toBeVisible();
    await expect(page.locator("option", { hasText: "Finance" })).toBeAttached();
    await page.getByRole("button", { name: "Top 5 selling SKUs by revenue" }).click();
    await expect(page.getByTestId("ask-stages")).toBeVisible();
    await expect(page.getByText("Retrieve")).toBeVisible();
    await expect(page.getByText("Generate")).toBeVisible();
    await expect(page.getByTestId("ask-stages-cancel")).toBeVisible();
    expect(askHeaders["x-dms-ask-id"] || askHeaders["X-Dms-Ask-Id"]).toBeTruthy();
    await page.getByTestId("ask-stages-cancel").click();
    await expect(page.getByText("Cancelled.")).toBeVisible();
  });
});
