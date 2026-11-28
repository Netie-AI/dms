import { expect, test, type Page, type Route } from "@playwright/test";

const FINANCE = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  name: "Finance",
  source_count: 2,
  member_count: 1,
};

const ANSWER = {
  answer_id: "ans_walk",
  text: "Stock for RS622KK is 12.5.\n\nInsights:\n- The row is the only figure.",
  badge: "L2_VALIDATED",
  abstained: false,
  assumptions: [],
  values: [],
  rows: [{ sku: "RS622KK", qty: 12.5 }],
  chart: { kind: "bar", x: "sku", y: "qty", title: "Stock" },
  contributing_sources: [],
  as_of: "2026-11-28T00:00:00Z",
  sql_used: "SELECT sku, qty FROM inventory",
};

const GOVERN = [
  { path: "/ontology", heading: "Ontology" },
  { path: "/amend", heading: "Amend" },
  { path: "/audit", heading: "Audit" },
  { path: "/trust", heading: "Trust" },
];

async function install(page: Page, health: Record<string, unknown>) {
  await page.route("**/api/**", async (route: Route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (url.includes("/health")) {
      await route.fulfill({ json: health });
      return;
    }
    if (url.includes("/v1/spaces") && method === "GET") {
      await route.fulfill({
        json: { spaces: [FINANCE], persisted: true, hint: null },
      });
      return;
    }
    if (url.includes("/v1/chat/ask") && method === "POST") {
      await route.fulfill({ json: ANSWER });
      return;
    }
    if (url.includes("/studio/chat/plan")) {
      await route.fulfill({
        status: 503,
        json: {
          ok: false,
          sql_ran: false,
          code: "cortex_unavailable",
          message: "unreachable: ConnectError",
        },
      });
      return;
    }
    if (url.includes("/export.xlsx")) {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "gate_unavailable" }),
      });
      return;
    }
    if (url.includes("/library/tree")) {
      await route.fulfill({
        json: {
          space_id: FINANCE.id,
          space_name: FINANCE.name,
          nodes: [
            {
              id: "folder:warehouse",
              kind: "folder",
              label: "Warehouse",
              children: [
                {
                  id: "warehouse:inventory",
                  kind: "leaf",
                  label: "inventory",
                  meta: { row_count: 1 },
                },
              ],
            },
          ],
        },
      });
      return;
    }
    await route.fulfill({ status: 200, json: {} });
  });
}

const LIVE_DOWN = {
  status: "ok",
  ask_mode: "live",
  demo_fallback: false,
  database_configured: false,
  backend: "memory",
  database: { persistent: false, hint: "In memory for this walk." },
  dependencies: {
    cortex: { ok: false, contract_routes: false, error: "connection refused" },
    openvault: { ok: false, trust: { ok: false, jwks_ok: false } },
  },
};

test.describe("Studio sell walk", () => {
  test("ask, chart, insights, and exports use the answer on screen", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await install(page, { ...LIVE_DOWN, ask_mode: "demo" });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: /Ask your company's data/ })).toBeVisible();
    await expect(page.getByText(/D:\\Cortex/)).toHaveCount(0);
    const ask = page.getByRole("button", { name: "Ask", exact: true });
    await expect(ask).toBeDisabled();
    await page.getByPlaceholder(/Ask about your data/i).fill("Stock for RS622KK");
    await expect(ask).toBeEnabled();
    await ask.click();
    await expect(page.getByText("Stock for RS622KK is 12.5.").first()).toBeVisible();
    await page.getByTestId("answer-chart-button").click();
    await expect(page.getByText("Stock").first()).toBeVisible();
    await page.getByTestId("answer-insights-button").click();
    await expect(page.getByText("The row is the only figure.").first()).toBeVisible();
    const word = page.waitForEvent("download");
    await page.getByTestId("answer-word").click();
    expect((await word).suggestedFilename()).toMatch(/\.docx$/);
    const slides = page.waitForEvent("download");
    await page.getByTestId("answer-slides").click();
    expect((await slides).suggestedFilename()).toMatch(/\.pptx$/);
    const excel = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download Excel" }).click();
    expect((await excel).suggestedFilename()).toMatch(/\.xlsx$/);
    await expect(page.getByText(/Saved the rows on screen/)).toBeVisible();
    await page.getByTestId("answer-create-app").click();
    await expect(page.getByTestId("answer-app")).toBeVisible();
    await expect(page.getByTestId("answer-app")).toContainText("RS622KK");
    expect(errors).toEqual([]);
  });

  test("new menu creates an app and reaches connector and settings", async ({ page }) => {
    await install(page, { ...LIVE_DOWN, ask_mode: "demo" });
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await page.getByRole("button", { name: "+ New" }).click();
    await page.getByRole("button", { name: "Create app" }).click();
    await expect(page.getByTestId("deliverable-note")).toContainText("Ask a question first");
    await page.getByRole("button", { name: "+ New" }).click();
    await page.getByRole("button", { name: "Connect a database" }).click();
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();
    await expect(page.getByText("Connector", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "+ New" }).click();
    await page.getByRole("button", { name: "Settings" }).click();
    await expect(page.getByRole("heading", { name: "Admin", level: 1 })).toBeVisible();
    for (const item of GOVERN) {
      await page.goto(item.path);
      await expect(page.getByRole("heading", { name: item.heading, level: 1 })).toBeVisible();
    }
  });

  test("a closed plan is a sentence, not a transport dump", async ({ page }) => {
    await install(page, { ...LIVE_DOWN, ask_mode: "demo" });
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await page.goto("/studio");
    await page.getByTestId("studio-chat-question").fill("What is total stock value by category?");
    await page.getByTestId("studio-chat-plan").click();
    await expect(page.getByTestId("studio-chat-error")).toContainText("not reachable");
    await expect(page.getByTestId("studio-chat-error")).not.toContainText("ConnectError");
    await expect(page.locator("body")).not.toContainText("D:\\Cortex");
  });

  test("engine-down ask mode does not show a machine path", async ({ page }) => {
    await install(page, LIVE_DOWN);
    await page.goto("/");
    await expect(page.getByText(/not reachable/)).toBeVisible();
    await expect(page.locator("body")).not.toContainText("D:\\");
    await expect(page.locator("body")).not.toContainText("ConnectError");
  });
});
