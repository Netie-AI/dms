import { expect, test, type Page } from "@playwright/test";

const FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc";

const BENCH = {
  enabled: true,
  space_id: FINANCE,
  items: [
    {
      id: "b1",
      question: "bench synth count",
      gold_sql: "SELECT qty FROM bench_sku",
      status: "valid",
      invalid_reason: "",
      source: "steward",
    },
  ],
  latest_run: {
    id: "run1",
    status: "done",
    progress_done: 1,
    progress_total: 1,
    tally: {
      n: 1,
      graded: 1,
      coverage: 1,
      correct: 1,
      WRONG: 0,
      clarify: 0,
      reconfirm: 0,
      abstain: 0,
      refusal_wrong: 0,
      invalid_gold: 0,
    },
    results: [
      {
        id: "r1",
        question: "bench synth count",
        served_sql: "SELECT qty FROM bench_sku",
        outcome: "correct",
        reason: "match",
        gold_n: 1,
        served_n: 1,
        review: null,
        review_note: "",
      },
    ],
  },
};

async function install(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/benchmarks")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(BENCH),
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
        }),
      });
      return;
    }
    if (url.includes("/v1/spaces")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          spaces: [{ id: FINANCE, name: "Finance", source_count: 1, member_count: 1 }],
          persisted: false,
        }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

test.describe("FEAT-BENCH-SPACE-01", () => {
  test("the benchmarks tab renders a run", async ({ page }) => {
    await install(page);
    await page.goto("/trust");
    await page.getByTestId("trust-bench-tab").click();
    await expect(page.getByTestId("trust-bench-wrong")).toHaveText("WRONG 0");
    await expect(page.getByTestId("trust-bench-coverage")).toContainText("coverage 100.0%");
    await expect(page.getByText("bench synth count")).toBeVisible();
    await expect(page.getByTestId("trust-bench-cancel")).toHaveCount(0);
  });
});
