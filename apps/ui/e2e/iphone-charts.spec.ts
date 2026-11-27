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

/** Text of this chart that renders under `min` px: every element with its own text, plus SVG text. */
const smallText = (root: Locator, min = 12) =>
  root.evaluate(
    (r, floor) => {
      const out: string[] = [];
      for (const el of [r as Element, ...r.querySelectorAll("*")]) {
        const own = [...el.childNodes].some((n) => n.nodeType === 3 && (n.textContent ?? "").trim());
        if (!own) continue;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs < floor) out.push(`${el.tagName} "${(el.textContent ?? "").trim().slice(0, 24)}" ${fs}px`);
      }
      return out;
    },
    min,
  );

/** Replace the answer's rows and chart with awkward ones. */
async function mockAsk(page: Page, rows: Record<string, unknown>[], chart: Record<string, unknown> | null) {
  await page.route("**/v1/chat/ask", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.rows = rows;
    body.chart = chart;
    await route.fulfill({ response: res, json: body });
  });
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
      // A ranked row spans the box, so the tapping dimension is its height.
      const b = await box(bar);
      expect(b.height, await bar.getAttribute("aria-label") ?? "").toBeGreaterThanOrEqual(TARGET);
      expect(b.width).toBeGreaterThan(PHONE.width * 0.7);
    }
    expect(await smallText(chart)).toEqual([]);

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
    expect(await smallText(chart)).toEqual([]);
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
    expect(await smallText(chart)).toEqual([]);
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
    // The long label is cut with an ellipsis inside its own column: one line, never wider than
    // the bar button it sits in, and still a real overflow (the full text is in the readout).
    const cut = await long.locator("[data-chart-label]").evaluate((el) => {
      const cs = getComputedStyle(el);
      const col = el.parentElement!.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      return {
        overflow: cs.textOverflow,
        wrap: cs.whiteSpace,
        inside: r.x >= col.x - 0.5 && r.x + r.width <= col.x + col.width + 0.5,
        oneLine: el.scrollHeight <= el.clientHeight + 1,
        clipped: el.scrollWidth > el.clientWidth,
      };
    });
    expect(cut).toEqual({ overflow: "ellipsis", wrap: "nowrap", inside: true, oneLine: true, clipped: true });
    expect(await smallText(chart)).toEqual([]);
    await page.screenshot({ path: `${SHOTS}/bar-many.png` });
    await noPageOverflow(page);
  });
  test("one row, one value: no chart below lg, the table still states the value", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "What was total revenue?");
    const rows = answer.getByTestId("rows-block").first().locator("tbody tr");
    await expect(rows).toHaveCount(1);
    await expect(rows.first().locator("td").last()).toHaveText(/\d/);
    await expect(answer.getByTestId("answer-chart")).toHaveCount(0);
    await expect(answer.locator("svg[aria-label]")).toHaveCount(0);
    await noPageOverflow(page);
  });

  const NEG_ROWS = [
    { sku: "SKU-A", revenue_myr: 5000 },
    { sku: "SKU-NEG", revenue_myr: -2500 },
    { sku: "SKU-ZERO", revenue_myr: 0 },
  ];

  for (const kind of ["bar", "hbar"] as const) {
    test(`${kind}: a negative value is drawn differently from zero and said to be negative`, async ({ page }) => {
      await mockAsk(page, NEG_ROWS, { kind, x: "sku", y: "revenue_myr", title: "Signed" });
      await page.goto("/");
      await page.waitForLoadState("networkidle");
      const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
      const chart = answer.getByTestId("answer-chart").first();
      await chart.scrollIntoViewIfNeeded();
      await page.waitForTimeout(700); // the hbar width transition
      const bars = chart.getByRole("button");
      await expect(bars).toHaveCount(3);
      const marks = chart.getByTestId("chart-mark");
      const look = (i: number) =>
        marks.nth(i).evaluate((el) => {
          const cs = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return { image: cs.backgroundImage, w: r.width, h: r.height };
        });
      const [pos, neg, zero] = [await look(0), await look(1), await look(2)];
      expect(pos.image).toBe("none");
      expect(zero.image).toBe("none");
      expect(neg.image).toContain("repeating-linear-gradient");
      // Not the 2px sliver a zero gets: a negative has a size of its own.
      const size = kind === "bar" ? "h" : "w";
      expect(zero[size]).toBeLessThanOrEqual(6);
      expect(neg[size]).toBeGreaterThan(zero[size] + 10);
      await expect(bars.nth(1)).toHaveAttribute("aria-label", "SKU-NEG: -2,500, negative");
      await expect(bars.nth(0)).toHaveAttribute("aria-label", "SKU-A: 5,000");
      await expect(bars.nth(2)).toHaveAttribute("aria-label", "SKU-ZERO: 0");
      await bars.nth(1).tap();
      const readout = chart.getByTestId("chart-readout");
      await expect(readout).toContainText("SKU-NEG");
      await expect(readout).toContainText("negative");
      await expect(readout).toContainText("-2,500");
      expect(await smallText(chart)).toEqual([]);
    });

    test(`${kind}: a row with no label and no value gets a placeholder, never an empty readout`, async ({ page }) => {
      await mockAsk(
        page,
        [
          { sku: null, revenue_myr: null },
          { sku: "SKU-A", revenue_myr: 10 },
          { sku: "SKU-B", revenue_myr: 5 },
        ],
        { kind, x: "sku", y: "revenue_myr", title: "Gaps" },
      );
      await page.goto("/");
      await page.waitForLoadState("networkidle");
      const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
      const chart = answer.getByTestId("answer-chart").first();
      await chart.scrollIntoViewIfNeeded();
      const bar = chart.getByRole("button").first();
      await expect(bar).toHaveAttribute("aria-label", "(no label): no value");
      await expect(bar.locator("[data-chart-label]")).toHaveText("(no label)");
      await bar.tap();
      const readout = chart.getByTestId("chart-readout");
      await expect(readout).toContainText("(no label)");
      await expect(readout).toContainText("no value");
      expect(await readout.evaluate((el) => (el.textContent ?? "").trim().length)).toBeGreaterThan(8);
    });
  }

  test("a very long label is clamped to 3 lines in the readout, with the full text on title", async ({ page }) => {
    const longWords = Array.from({ length: 80 }, (_, i) => `segment${i}`).join(" ");
    const longToken = "X".repeat(900);
    await mockAsk(
      page,
      [
        { sku: longWords, revenue_myr: 3 },
        { sku: longToken, revenue_myr: 2 },
        { sku: "SKU-C", revenue_myr: 1 },
      ],
      { kind: "bar", x: "sku", y: "revenue_myr", title: "Long" },
    );
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
    const chart = answer.getByTestId("answer-chart").first();
    await chart.scrollIntoViewIfNeeded();
    const bars = chart.getByRole("button");
    for (const [i, text] of [[0, longWords], [1, longToken]] as const) {
      await bars.nth(i).tap();
      const readout = chart.getByTestId("chart-readout");
      const name = readout.locator("span").first();
      await expect(name).toHaveAttribute("title", text);
      const m = await name.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { lh: parseFloat(cs.lineHeight), h: el.getBoundingClientRect().height, clamp: cs.webkitLineClamp };
      });
      expect(m.clamp).toBe("3");
      expect(m.h).toBeLessThanOrEqual(m.lh * 3 + 1);
      expect((await box(readout)).height).toBeLessThanOrEqual(m.lh * 3 + 40);
    }
    await noPageOverflow(page);
  });

  test("a line chart: the dot at the maximum is not clipped by the scroller, text is 12px or more", async ({ page }) => {
    await mockAsk(
      page,
      [100, 250, 900, 400, 300, 150].map((v, i) => ({ day: `2026-10-0${i + 1}`, revenue_myr: v })),
      { kind: "line", x: "day", y: "revenue_myr", title: "Daily revenue" },
    );
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
    const chart = answer.getByTestId("answer-chart").first();
    await chart.scrollIntoViewIfNeeded();
    const sc = await box(chart.getByTestId("chart-scroll"));
    expect(await smallText(chart), "includes the hint under the chart").toEqual([]);
    const dots = chart.locator('[data-testid="chart-scroll"] button span.rounded-full');
    await expect(dots).toHaveCount(6);
    let topMost = Infinity;
    for (const d of await dots.all()) {
      const b = await box(d);
      expect(b.y, "dot top inside the scroller").toBeGreaterThanOrEqual(sc.y - 0.5);
      expect(b.y + b.height).toBeLessThanOrEqual(sc.y + sc.height + 0.5);
      topMost = Math.min(topMost, b.y);
    }
    // The highest dot has room above it, not just zero.
    expect(topMost - sc.y).toBeGreaterThanOrEqual(2);
    await chart.getByRole("button").nth(2).tap();
    const readout = chart.getByTestId("chart-readout");
    await expect(readout).toContainText("2026-10-03");
    await expect(readout).toContainText("900");
    expect(await smallText(chart)).toEqual([]);
    await page.screenshot({ path: `${SHOTS}/line.png` });
    await noPageOverflow(page);
  });
});

test.describe("IPHONE-PWA charts on desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  const DESKTOP = { width: 1280, height: 800 };

  /**
   * Reference values measured on a build of `main` at 1280x800 on 2026-11-12, before any phone
   * work touched this component. They are written here, not read from a second server, so the
   * tests run anywhere. If the desktop chart is changed on purpose, re-measure and update them.
   */
  const MAIN = {
    twoBar: { svg: 553, panel: 607, width: 448, label: "10px", title: { fs: "11px", lh: "16.5px", h: 16.5 } },
    hbar: {
      panelH: 186.5,
      ulH: 132,
      rowH: [20, 20, 20, 20, 20],
      labelFs: "14px",
      valueFs: "14px",
      labelLh: "20px",
      titleFs: "11px",
      titleLh: "16.5px",
      trackH: 12,
    },
    oneValue: { panelH: 1090.5, svgH: 1036, text: ["total_reve"] },
  };

  /** Ask `q` at desktop size and let the bar transition finish. */
  async function answerTo(page: Page, q: string) {
    await page.setViewportSize(DESKTOP);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, q);
    await page.waitForTimeout(800);
    return answer;
  }

  test("1280x800: the 2-bar SVG chart is unchanged from main, size and text", async ({ page }) => {
    const answer = await answerTo(page, "Divide the revenue by 5");
    const svg = answer.locator("svg[aria-label]").first();
    await expect(svg).toBeVisible();
    const s = await box(svg);
    const panel = svg.locator("xpath=..");
    const pb = await box(panel);
    const title = await panel.locator("p").first().evaluate((t) => {
      const cs = getComputedStyle(t);
      return { fs: cs.fontSize, lh: cs.lineHeight, h: t.getBoundingClientRect().height };
    });
    const label = await svg.locator("text").first().evaluate((t) => getComputedStyle(t).fontSize);
    expect({
      svg: Math.round(s.height),
      panel: Math.round(pb.height * 10) / 10,
      width: Math.round(s.width),
      label,
      title,
    }).toEqual(MAIN.twoBar);
  });

  test("1280x800: the ranked hbar chart is unchanged from main: row height, font size, panel height", async ({ page }) => {
    const answer = await answerTo(page, "Top 5 selling SKUs by revenue");
    const live = await answer.evaluate((a) => {
      const ul = [...a.querySelectorAll("ul")].find((u) => u.querySelector("li [class*='h-3']"))!;
      const panel = ul.parentElement!;
      const fs = (e: Element) => getComputedStyle(e).fontSize;
      const lh = (e: Element) => getComputedStyle(e).lineHeight;
      const lis = [...ul.querySelectorAll("li")];
      const first = lis[0];
      const spans = first.querySelectorAll("span");
      return {
        panelH: Math.round(panel.getBoundingClientRect().height * 10) / 10,
        ulH: Math.round(ul.getBoundingClientRect().height * 10) / 10,
        rowH: lis.map((l) => Math.round(l.getBoundingClientRect().height * 10) / 10),
        labelFs: fs(spans[0]),
        valueFs: fs(spans[spans.length - 1]),
        labelLh: lh(spans[0]),
        titleFs: fs(panel.querySelector("p")!),
        titleLh: lh(panel.querySelector("p")!),
        trackH: Math.round((first.querySelector("[class*='h-3']") as HTMLElement).getBoundingClientRect().height * 10) / 10,
      };
    });
    expect(live).toEqual(MAIN.hbar);
  });

  test("1280x800: one row, one value still draws main's chart", async ({ page }) => {
    const answer = await answerTo(page, "What was total revenue?");
    const live = await answer.evaluate((a) => {
      const panel = [...a.querySelectorAll("div.mt-4")].find(
        (d) => !d.querySelector("table") && (d.querySelector("svg[aria-label]") || d.querySelector("ul")),
      );
      if (!panel) return null;
      const svg = panel.querySelector("svg");
      return {
        panelH: Math.round(panel.getBoundingClientRect().height * 10) / 10,
        svgH: svg ? Math.round(svg.getBoundingClientRect().height) : null,
        text: [...panel.querySelectorAll("text")].map((t) => t.textContent),
      };
    });
    expect(live).toEqual(MAIN.oneValue);
  });

  test("1280x800: SVG bars are keyboard buttons (Enter, Space) and clickable", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Divide the revenue by 5");
    const chart = answer.getByTestId("answer-chart").first();
    const bars = chart.locator('svg g[role="button"]');
    await expect(bars).toHaveCount(2);
    const first = await tableRow(answer, "Total");
    const second = await tableRow(answer, "÷ 5");
    const readout = chart.getByTestId("chart-readout");
    await expect(readout).toHaveCount(0);

    await bars.nth(0).focus();
    await expect(bars.nth(0)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(readout).toContainText(first[first.length - 1]);
    await expect(bars.nth(0)).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Enter");
    await expect(readout).toHaveCount(0);
    await page.keyboard.press("Space");
    await expect(readout).toContainText(first[first.length - 1]);
    // A click on another bar moves the selection.
    await bars.nth(1).click();
    await expect(readout).toContainText("÷ 5");
    await expect(readout).toContainText(second[second.length - 1]);
    await expect(readout).not.toContainText("Total");
    await expect(bars.nth(1)).toHaveAttribute("aria-pressed", "true");
    await expect(bars.nth(0)).toHaveAttribute("aria-pressed", "false");
    // The hbar rows on desktop are focusable buttons too.
    await page.getByPlaceholder(/Ask about your data/).fill("Top 5 selling SKUs by revenue");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("answer-message")).toHaveCount(2, { timeout: 20_000 });
    const rows = page.getByTestId("answer-message").last().getByTestId("answer-chart").first().getByRole("button");
    await expect(rows).toHaveCount(5);
    await rows.nth(1).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("chart-readout").last()).toContainText("SKU-ALPHA");
  });

  test("1280x800: a negative value and a missing label draw exactly as before", async ({ page }) => {
    await mockAsk(
      page,
      [
        { sku: null, revenue_myr: 5000 },
        { sku: "SKU-NEG", revenue_myr: -2500 },
        { sku: "SKU-ZERO", revenue_myr: 0 },
      ],
      { kind: "bar", x: "sku", y: "revenue_myr", title: "Signed" },
    );
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const answer = await askQuestion(page, "Top 5 selling SKUs by revenue");
    const chart = answer.getByTestId("answer-chart").first();
    const rects = await chart.locator('svg rect[fill="var(--color-accent)"]').evaluateAll((els) => els.map((e) => e.getAttribute("height")));
    expect(rects[1]).toBe(rects[2]); // negative and zero: the same 2 unit minimum, as in the reference
    expect(await chart.evaluate((el) => el.innerHTML.includes("repeating-linear-gradient"))).toBe(false);
    // The empty label draws as empty text in the SVG, as the reference did.
    expect(await chart.locator("svg text").first().textContent()).toBe("");
  });
});
