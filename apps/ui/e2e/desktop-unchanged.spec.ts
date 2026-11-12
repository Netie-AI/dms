import { expect, test, type Page } from "@playwright/test";

/**
 * The phone work (IPHONE-PWA-01..03) is scoped below lg. A comparison of this
 * branch against main at 1280px found desktop changes that had slipped in; each
 * one is pinned here on the rendered page so it cannot come back.
 */

test.use({ viewport: { width: 1280, height: 800 } });

const UNAVAILABLE = { ok: false, error: "unreachable: ConnectError", hint: "Start the engine, then Refresh." };

async function stubOntologyDown(page: Page) {
  await page.route("**/api/v1/ontology**", (route) =>
    route.fulfill({ json: { ...UNAVAILABLE, section: "summary" } }),
  );
}

async function operate(page: Page, route: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
  await page.evaluate((r) => {
    history.pushState({}, "", r);
    dispatchEvent(new PopStateEvent("popstate"));
  }, route);
  await page.waitForLoadState("networkidle");
}

test("the scope chip spans the composer, like the question field under it", async ({ page }) => {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const chip = (await page.getByTestId("scope-chip").boundingBox())!;
  const form = (await page.locator("form").boundingBox())!;
  expect(Math.round(chip.x)).toBe(Math.round(form.x));
  expect(Math.round(chip.width)).toBe(Math.round(form.width));
});

test("desktop has no phone chrome: no tab bar, no install hint, hamburger present", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("tab-bar")).toHaveCount(0);
  await expect(page.getByTestId("install-hint")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Toggle sidebar" })).toBeVisible();
  await expect(page.getByTestId("source-panel")).not.toHaveAttribute("role", "dialog");
  await expect(page.locator("main").first()).not.toHaveAttribute("inert", "");
});

test("Spaces shows no derived-ontology card (it is a phone screen for now)", async ({ page }) => {
  await page.goto("/spaces");
  await expect(page.getByRole("heading", { name: "Spaces", level: 1 })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("space-ontology-card")).toHaveCount(0);
});

test("Ontology with the engine down keeps the notice, the tiles and the tabs", async ({ page }) => {
  await stubOntologyDown(page);
  await operate(page, "/ontology");
  await expect(page.getByText("Ontology unavailable — Cortex did not answer")).toBeVisible();
  await expect(page.getByTestId("ontology-unavailable")).toHaveCount(0);
  await expect(page.getByText("Object types").first()).toBeVisible();
  await expect(page.getByRole("tab", { name: "Map" })).toBeVisible();
});

test("Ontology tabs keep their 8px gap and the filter sits on the same row", async ({ page }) => {
  await stubOntologyDown(page);
  await operate(page, "/ontology");
  const tabs = await page.getByRole("tab").all();
  const boxes = await Promise.all(tabs.map(async (t) => (await t.boundingBox())!));
  for (let i = 1; i < boxes.length; i += 1) {
    expect(Math.round(boxes[i].x - (boxes[i - 1].x + boxes[i - 1].width))).toBe(8);
    expect(Math.round(boxes[i].y)).toBe(Math.round(boxes[0].y));
  }
  const filter = (await page.getByPlaceholder(/Filter objects/).boundingBox())!;
  expect(filter.y).toBeLessThan(boxes[0].y + boxes[0].height);
  expect(filter.height).toBe(32);
});

test("the answer thread still ends at the newest answer's foot", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask about your data/).fill("Top 5 selling SKUs by revenue");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("answer-message")).toBeVisible({ timeout: 20_000 });
  // Tools stay in the header row above the sentence at lg.
  const tools = (await page.getByRole("button", { name: "Download CSV" }).boundingBox())!;
  const text = (await page.getByTestId("answer-text").boundingBox())!;
  expect(tools.y + tools.height).toBeLessThanOrEqual(text.y);
});
