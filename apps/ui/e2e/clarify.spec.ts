import { expect, test, type Page, type Route } from "@playwright/test";

const FINANCE = {
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  name: "Finance",
  source_count: 3,
  member_count: 1,
};

const OPS = {
  id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  name: "Warehouse Ops",
  source_count: 2,
  member_count: 1,
};

type AskHandler = (body: Record<string, unknown>) => unknown;

function envelope(over: Record<string, unknown>) {
  return {
    answer_id: "ans_e2e",
    text: "no number",
    badge: "ABSTAIN",
    abstained: true,
    assumptions: [],
    values: [],
    rows: [],
    contributing_sources: [],
    as_of: "2026-11-27T00:00:00Z",
    ...over,
  };
}

const CLARIFY = envelope({
  answer_id: "ans_clarify",
  text: "Which reading?",
  clarify: {
    clarify_id: "c1",
    question: "Which reading?",
    readings: [
      { reading_id: "r_month", label: "Revenue by month" },
      { reading_id: "r_country", label: "Revenue by country" },
    ],
  },
});

const SERVED = envelope({
  answer_id: "ans_done",
  text: "Reading served",
  badge: "L2_VALIDATED",
  abstained: false,
});

async function install(
  page: Page,
  asks: AskHandler[],
  accessStatus = 200,
  spaces: { id: string; name: string; source_count: number; member_count: number }[] = [
    FINANCE,
    OPS,
  ],
): Promise<Record<string, unknown>[]> {
  const seen: Record<string, unknown>[] = [];
  let n = 0;
  await page.route("**/api/**", async (route: Route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (method === "POST" && url.includes("/v1/chat/ask") && !url.includes("/confirm")) {
      const body = JSON.parse(route.request().postData() || "{}") as Record<string, unknown>;
      seen.push(body);
      const handler = asks[Math.min(n, asks.length - 1)];
      n += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(handler(body)),
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
    if (url.includes("access-requests")) {
      await route.fulfill({
        status: accessStatus,
        contentType: "application/json",
        body: accessStatus === 200 ? "{}" : "secret-row",
      });
      return;
    }
    if (url.includes("/v1/spaces")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ spaces, persisted: false }),
      });
      return;
    }
    if (url.includes("/v1/library/tree")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          space_id: FINANCE.id,
          space_name: FINANCE.name,
          nodes: [],
        }),
      });
      return;
    }
    if (url.includes("/v1/studio/chat/plan")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          plan: { provided: true, text: "plan", query_plan: { metric: "spend" } },
          ontology: {
            tables: { provided: true, text: null, items: ["orders"] },
            joins: { provided: true, text: null, items: [] },
            metrics: { provided: true, text: null, items: ["spend"] },
          },
          clarify: { provided: false, text: null, blocks: false },
        }),
      });
      return;
    }
    if (url.includes("/v1/studio/chat/confirm")) {
      const body = JSON.parse(route.request().postData() || "{}") as { question?: string };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ execute: true, question: body.question || "" }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  return seen;
}

test.describe("R1-UI-CLARIFY-01", () => {
  test("vague question shows chips and the pick runs that reading", async ({ page }) => {
    const seen = await install(page, [() => CLARIFY, () => SERVED]);
    await page.goto("/");
    await expect(page.getByText("API · ok")).toBeVisible();
    await page.getByPlaceholder(/Ask about your data/i).fill("How are sales");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("clarify-card")).toBeVisible();
    const month = page.getByRole("button", { name: "Revenue by month" });
    await expect(month).toBeVisible();
    await month.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Reading served").first()).toBeVisible();
    await expect(page.getByTestId("clarify-card")).toHaveCount(0);
    expect(seen[0]).not.toHaveProperty("clarify_id");
    expect(seen[0]).not.toHaveProperty("reading_id");
    expect(seen[1]).toMatchObject({
      question: "How are sales",
      clarify_id: "c1",
      reading_id: "r_month",
    });
  });

  test("ungranted table switches Space and re-asks", async ({ page }) => {
    const seen = await install(page, [
      () =>
        envelope({
          answer_id: "ans_access",
          text: "That table is not in this Space.",
          access_needed: {
            table: "orders",
            granted_in_spaces: [{ id: OPS.id, name: OPS.name }],
            can_request: true,
          },
        }),
      () => SERVED,
    ]);
    await page.goto("/");
    await expect(page.getByText("API · ok")).toBeVisible();
    await page.getByPlaceholder(/Ask about your data/i).fill("How are sales");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("access-card")).toBeVisible();
    await expect(page.getByTestId("access-card").getByText("Warehouse Ops")).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask the steward for access" })).toBeVisible();
    await page.getByRole("button", { name: "Switch to that Space" }).click();
    await expect(page.locator("#space-switcher")).toHaveValue(OPS.id);
    await expect(page.getByText("Reading served").first()).toBeVisible();
    expect(seen[1]).toMatchObject({ question: "How are sales", space_id: OPS.id });
    expect(seen[1]).not.toHaveProperty("clarify_id");
  });

  test("does not offer Switch to a Space the server did not return", async ({ page }) => {
    await install(
      page,
      [
        () =>
          envelope({
            answer_id: "ans_access",
            text: "That table is not in this Space.",
            access_needed: {
              table: "orders",
              granted_in_spaces: [
                { id: OPS.id, name: OPS.name },
                { id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", name: "Restricted" },
              ],
              can_request: false,
            },
          }),
      ],
      200,
      [FINANCE],
    );
    await page.goto("/");
    await expect(page.locator("#space-switcher option", { hasText: "Warehouse Ops" })).toHaveCount(0);
    await page.getByPlaceholder(/Ask about your data/i).fill("How are sales");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("access-card")).toBeVisible();
    await expect(page.getByTestId(`access-switch-${OPS.id}`)).toHaveCount(0);
    await expect(page.getByTestId("access-switch-eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Switch to that Space" })).toHaveCount(0);
    await expect(page.locator("#space-switcher")).not.toHaveValue(OPS.id);
  });

  test("Ask the steward is hidden until the route exists", async ({ page }) => {
    await install(
      page,
      [
        () =>
          envelope({
            text: "That table is not in this Space.",
            access_needed: {
              table: "orders",
              granted_in_spaces: [{ id: OPS.id, name: OPS.name }],
              can_request: false,
            },
          }),
      ],
    );
    await page.goto("/");
    await page.getByPlaceholder(/Ask about your data/i).fill("How are sales");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("access-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "Switch to that Space" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask the steward for access" })).toHaveCount(0);

    await install(
      page,
      [
        () =>
          envelope({
            text: "That table is not in this Space.",
            access_needed: {
              table: "orders",
              granted_in_spaces: [{ id: OPS.id, name: OPS.name }],
              can_request: true,
            },
          }),
      ],
      404,
    );
    await page.goto("/");
    await page.getByPlaceholder(/Ask about your data/i).fill("How are sales");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    const request = page.getByRole("button", { name: "Ask the steward for access" });
    await expect(request).toBeVisible();
    await request.click();
    await expect(request).toHaveCount(0);
    await expect(page.getByText("secret-row")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Switch to that Space" })).toBeVisible();
  });

  test("Studio confirm shows the same chips and the pick runs", async ({ page }) => {
    const seen = await install(page, [() => CLARIFY, () => SERVED]);
    await page.goto("/studio");
    await expect(page.getByTestId("studio-chat-turn")).toBeVisible();
    await page.getByTestId("studio-chat-question").fill("How are sales");
    await page.getByTestId("studio-chat-plan").click();
    await expect(page.getByTestId("studio-chat-confirm")).toBeEnabled();
    await page.getByTestId("studio-chat-confirm").click();
    await expect(page.getByTestId("clarify-card")).toBeVisible();
    await page.getByRole("button", { name: "Revenue by country" }).click();
    await expect(page.getByTestId("studio-chat-result").getByText("Reading served")).toBeVisible();
    expect(seen[1]).toMatchObject({ clarify_id: "c1", reading_id: "r_country" });
  });
});

test.describe("R1-UI-CLARIFY-01 phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("chips stay on screen and the tap target is at least 44px", async ({ page }) => {
    await install(page, [() => CLARIFY]);
    await page.goto("/");
    await page.getByPlaceholder(/Ask about your data/i).fill("How are sales");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    const month = page.getByRole("button", { name: "Revenue by month" });
    await expect(month).toBeVisible();
    const box = await month.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
