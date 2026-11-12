import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * IPHONE-PWA charts: an answer chart is usable by touch at 390x844. A tap selects a
 * bar and the exact figure appears in a readout under the chart (no hover), every
 * bar is a 44px target, the chart is tall enough to read, and a long category list
 * scrolls inside the chart box instead of widening the page. Assertions are on
 * rendered boxes and text, and the readout number is read from the rows table.
 */

const PHONE = { width: 390, height: 844 };
const TARGET = 44;
const FROZEN = "http://127.0.0.1:4174";
const SHOTS = "/tmp/claude-0/-home-user/4e000c9a-b8cf-5fd7-a03e-e059c8322116/scratchpad/r3/charts";

async function box(loc: Locator) {
  const b = await loc.boundingBox();
  expect(b).toBeTruthy();
  return b!;
}

async function askQuestion(page: Page, q: string) {
  await page.getByPlaceholder(/Ask about your data/).fill(q);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = page.getByTestId("answer-message").last();
  await expect(answer).toBeVisible({ timeout: 20_000 });
  await expect(answer.locator("table").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
  return answer;
}

/** The text of the cell(s) of the table row whose first data cell is `key`. */
async function tableRow(answer: Locator, key: string): Promise<string[]> {
  const row = answer
    .locator("table")
    .first()
    .locator("tbody tr", { has: answer.page().getByRole("cell", { name: key, exact: true }) });
  return (await row.locator("td").allInnerTexts()).map((t) => t.trim());
}

async function noPageOverflow(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

test.describe("IPHONE-PWA charts at 390x844", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

  test("a ranked chart is tall, its bars are 44px targets, tap shows the exact figure", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
    const chart = answer.getByTestId("answer-chart").first();
    await chart.scrollIntoViewIfNeeded();
    expect((await box(chart)).height).toBeGreaterThanOrEqual(200);
    expect((await box(chart)).width).toBeGreaterThan(PHONE.width * 0.8);

    const bars = chart.getByRole("button");
    await expect(bars).toHaveCount(5);
    for (const bar of await bars.all()) {
      const b = await box(bar);
      expect(Math.max(b.height, b.width), await bar.getAttribute("aria-label") ?? "").toBeGreaterThanOrEqual(TARGET);
    }

    // The figure the table shows for SKU-BETA, read from the page.
    const cells = await tableRow(answer, "SKU-BETA");
    const tableValue = cells[cells.length - 1];
    expect(tableValue).toMatch(/\d/);
    await expect(bars.first()).toHaveAttribute("aria-label", `SKU-BETA: ${tableValue}`);

    await expect(chart.getByTestId("chart-readout")).toHaveCount(0);
    await bars.first().tap();
    const readout = chart.getByTestId("chart-readout");
    await expect(readout).toBeVisible();
    await expect(readout).toContainText("SKU-BETA");
    await expect(readout).toContainText(tableValue);
    await expect(bars.first()).toHaveAttribute("aria-pressed", "true");
    // The readout sits directly under the bars, inside the chart box.
    const rb = await box(readout);
    const lastBar = await box(bars.last());
    expect(rb.y).toBeGreaterThanOrEqual(lastBar.y + lastBar.height - 1);
    await chart.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${SHOTS}/hbar-selected.png` });

    // Another bar moves the selection.
    await bars.nth(1).tap();
    await expect(readout).toContainText("SKU-ALPHA");
    await expect(readout).not.toContainText("SKU-BETA");
    await bars.first().tap();
    await expect(readout).toContainText("SKU-BETA");

    // Tapping the selected one clears it.
    await bars.first().tap();
    await expect(chart.getByTestId("chart-readout")).toHaveCount(0);
    await expect(bars.first()).toHaveAttribute("aria-pressed", "false");
    await noPageOverflow(page);
  });

  test("keyboard: bars are buttons labelled label: value, Enter and Space select", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
    const chart = answer.getByTestId("answer-chart").first();
    const bars = chart.getByRole("button");
    await bars.nth(2).focus();
    await expect(bars.nth(2)).toBeFocused();
    await page.keyboard.press("Enter");
    const readout = chart.getByTestId("chart-readout");
    await expect(readout).toContainText("RS622XK");
    await expect(readout).toContainText("3,915");
    await page.keyboard.press("Enter");
    await expect(chart.getByTestId("chart-readout")).toHaveCount(0);
    await page.keyboard.press("Space");
    await expect(readout).toContainText("RS622XK");
  });

  test("a two-bar chart: bars are tall targets and the readout shows the value", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Divide the revenue by 5");
    const chart = answer.getByTestId("answer-chart").first();
    await chart.scrollIntoViewIfNeeded();
    expect((await box(chart)).height).toBeGreaterThanOrEqual(200);
    const bars = chart.getByRole("button");
    await expect(bars).toHaveCount(2);
    for (const bar of await bars.all()) {
      const b = await box(bar);
      expect(Math.max(b.height, b.width)).toBeGreaterThanOrEqual(TARGET);
      expect(Math.min(b.height, b.width)).toBeGreaterThanOrEqual(TARGET);
    }
    const second = await tableRow(answer, "÷ 5");
    await bars.nth(1).tap();
    const readout = chart.getByTestId("chart-readout");
    await expect(readout).toContainText("÷ 5");
    await expect(readout).toContainText(second[second.length - 1]);
    await bars.nth(0).tap();
    const first = await tableRow(answer, "Total");
    await expect(readout).toContainText(first[first.length - 1]);
    await page.screenshot({ path: `${SHOTS}/bar-two.png` });
    await noPageOverflow(page);
  });

  test("more than 8 categories scroll inside the chart box and never widen the page", async ({ page }) => {
    await page.route("**/v1/chat/ask", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.rows = Array.from({ length: 12 }, (_, i) => ({
        sku: i === 3 ? "A very long category name that cannot fit" : `SKU-${String.fromCharCode(65 + i)}`,
        revenue_myr: i === 5 ? 0 : (12 - i) * 1234.5,
      }));
      body.chart = { kind: "bar", x: "sku", y: "revenue_myr", title: "Twelve" };
      await route.fulfill({ response: res, json: body });
    });
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
    const chart = answer.getByTestId("answer-chart").first();
    await chart.scrollIntoViewIfNeeded();
    const bars = chart.getByRole("button");
    await expect(bars).toHaveCount(12);
    const scroller = chart.getByTestId("chart-scroll");
    const dims = await scroller.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
    expect(dims.sw).toBeGreaterThan(dims.cw);
    await expect(chart.getByTestId("chart-scroll-cue")).toBeVisible();
    expect((await box(chart)).width).toBeLessThanOrEqual(PHONE.width);
    await noPageOverflow(page);
    expect((await box(chart)).height).toBeGreaterThanOrEqual(200);
    // The zero-value bar is still a 44px target and tappable.
    const zero = bars.nth(5);
    await zero.scrollIntoViewIfNeeded();
    const zb = await box(zero);
    expect(zb.width).toBeGreaterThanOrEqual(TARGET);
    expect(zb.height).toBeGreaterThanOrEqual(TARGET);
    await zero.tap();
    await expect(chart.getByTestId("chart-readout")).toHaveText(/^SKU-F\s*0$/);
    // Long labels are cut on the bar and complete in the readout.
    const long = bars.nth(3);
    await long.scrollIntoViewIfNeeded();
    await expect(long).toHaveAttribute("aria-label", /^A very long category name that cannot fit: /);
    await long.tap();
    await expect(chart.getByTestId("chart-readout")).toContainText("A very long category name that cannot fit");
    const small = await chart.evaluate((el) => {
      const out: string[] = [];
      for (const t of el.querySelectorAll<HTMLElement>("[data-chart-label]")) {
        if (parseFloat(getComputedStyle(t).fontSize) < 11) out.push(t.textContent ?? "");
      }
      return out;
    });
    expect(small).toEqual([]);
    await page.screenshot({ path: `${SHOTS}/bar-many.png` });
    await noPageOverflow(page);
  });
});

test.describe("IPHONE-PWA charts on desktop", () => {
  test("1280x800: the chart renders at the same height as the frozen build", async ({ browser }) => {
    async function measure(base: string) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      await page.goto(`${base}/`);
      await page.waitForLoadState("networkidle");
      const answer = await askQuestion(page, "Divide the revenue by 5");
      const svg = answer.locator("svg[aria-label]").first();
      await expect(svg).toBeVisible();
      const s = await box(svg);
      const panel = await box(svg.locator("xpath=.."));
      const label = await svg.locator("text").first().evaluate((t) => getComputedStyle(t).fontSize);
      await ctx.close();
      return { svg: Math.round(s.height), panel: Math.round(panel.height), width: Math.round(s.width), label };
    }
    const live = await measure("http://127.0.0.1:3000");
    const frozen = await measure(FROZEN);
    expect(live.svg).toBeGreaterThan(0);
    expect(live).toEqual(frozen);
  });
});
