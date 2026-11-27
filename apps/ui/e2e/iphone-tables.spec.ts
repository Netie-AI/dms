import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * IPHONE-TABLES: result tables read one-handed at 390x844. The first data column
 * stays in view while the rest scrolls, a cue says more columns hide to the right,
 * the pager is a 44px target, and no table text is under 12px. Cells are compared
 * with the rows the API returned (repo rule 10), not with class names.
 */

const PHONE = { width: 390, height: 844 };
const TARGET = 44;
const PREVIEW = /\/api\/v1\/library\/warehouse\/transactions\/preview/;

type Preview = { columns: string[]; rows: Record<string, unknown>[]; row_count: number };

async function box(loc: Locator) {
  const b = await loc.boundingBox();
  expect(b).toBeTruthy();
  return b!;
}

/** Open the Library; it selects the 7-column `transactions` warehouse table. */
async function openTransactions(page: Page): Promise<Preview> {
  const resp = page.waitForResponse((r) => PREVIEW.test(r.url()) && r.status() === 200);
  await page.goto("/library");
  const preview = (await (await resp).json()) as Preview;
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("library-detail").locator("tbody tr").first()).toBeVisible();
  return preview;
}

type Theme = "cream" | "graphite";

/** The app reads its theme from localStorage at start-up. */
async function setTheme(page: Page, theme: Theme) {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("dms-theme", t);
    } catch {
      /* ignore */
    }
  }, theme);
}

/** Text of this component that renders under `min` px (own text nodes only). */
const smallText = (root: Locator, min = 12) =>
  root.evaluate(
    (r, floor) => {
      const out: string[] = [];
      for (const el of [r as HTMLElement, ...r.querySelectorAll<HTMLElement>("*")]) {
        const own = [...el.childNodes].some((n) => n.nodeType === 3 && (n.textContent ?? "").trim());
        if (!own) continue;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs < floor) out.push(`${el.tagName} "${(el.textContent ?? "").trim().slice(0, 24)}" ${fs}px`);
      }
      return out;
    },
    min,
  );

/** Answer rows come from the API; swap them (and drop the chart) to build awkward tables. */
async function mockAsk(page: Page, rows: Record<string, unknown>[]) {
  await page.route("**/v1/chat/ask", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.rows = rows;
    body.chart = null;
    await route.fulfill({ response: res, json: body });
  });
}

async function ask(page: Page, q: string) {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByPlaceholder(/Ask about your data/).fill(q);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = page.getByTestId("answer-message").last();
  await expect(answer.getByTestId("rows-scroller").first()).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState("networkidle");
  return answer;
}

/** The Library preview endpoint, paged: `total` rows, `size` per page, rows named after their number. */
async function mockPreview(page: Page, total = 35, size = 10) {
  await page.route(PREVIEW, async (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get("offset") ?? 0);
    const res = await route.fetch();
    const body = await res.json();
    const n = Math.max(0, Math.min(size, total - offset));
    body.columns = ["txn_id", "branch", "quantity_kg", "unit_price_myr"];
    body.rows = Array.from({ length: n }, (_, i) => ({
      txn_id: `TXN-${offset + i + 1}`,
      branch: `Branch ${offset + i + 1}`,
      quantity_kg: (offset + i + 1) * 10,
      unit_price_myr: 2.5,
    }));
    body.row_count = total;
    body.offset = offset;
    body.limit = size;
    await route.fulfill({ response: res, json: body });
  });
}

const rowsLabel = (a: number, b: number, n: number) => `Rows ${a} to ${b} of ${n}`;

/** RGBA of any CSS colour (color-mix and color() included), via a canvas. */
const RGBA = `(css) => {
  const c = document.createElement("canvas");
  c.width = c.height = 1;
  const x = c.getContext("2d", { willReadFrequently: true });
  x.clearRect(0, 0, 1, 1);
  x.fillStyle = css;
  x.fillRect(0, 0, 1, 1);
  return Array.from(x.getImageData(0, 0, 1, 1).data);
}`;

const scroller = (page: Page) => page.getByTestId("library-detail").getByTestId("rows-scroller");

const scrollTable = (loc: Locator, to: "start" | "end") =>
  loc.evaluate((el, where) => {
    el.scrollLeft = where === "end" ? el.scrollWidth : 0;
  }, to);

async function noPageOverflow(page: Page) {
  const d = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
  }));
  expect(d.sw).toBe(d.cw);
}

test.describe("IPHONE-TABLES phone", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

  test("the first data column stays in view while the rest scrolls right", async ({ page }) => {
    await openTransactions(page);
    const sc = scroller(page);
    const table = sc.locator("table");
    expect((await box(table)).width).toBeGreaterThan(PHONE.width);
    await scrollTable(sc, "end");
    await expect.poll(() => sc.evaluate((el) => el.scrollLeft)).toBeGreaterThan(100);
    const sb = await box(sc);
    for (const cell of [table.locator("thead th").nth(1), table.locator("tbody tr").nth(3).locator("td").nth(1)]) {
      const c = await box(cell);
      expect(c.x).toBeGreaterThanOrEqual(sb.x - 0.5);
      expect(c.x + c.width).toBeLessThanOrEqual(PHONE.width);
      // Opaque and on top: the point at its centre hits the cell, not a column scrolled under it.
      const onTop = await cell.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return !!hit && el.contains(hit);
      });
      expect(onTop).toBe(true);
    }
    // And the last column really did scroll to the edge.
    const last = await box(table.locator("thead th").last());
    expect(last.x + last.width).toBeLessThanOrEqual(PHONE.width + 0.5);
  });

  test("a cue shows while columns hide to the right and goes at the end", async ({ page }) => {
    await openTransactions(page);
    const sc = scroller(page);
    const detail = page.getByTestId("library-detail");
    const cue = detail.getByTestId("more-columns-cue");
    await expect(cue).toBeVisible();
    const cb = await box(cue);
    const sb = await box(sc);
    expect(Math.round(cb.x + cb.width)).toBe(Math.round(sb.x + sb.width));
    // Visible for real: fully opaque, drawn with a gradient (toBeVisible ignores opacity).
    const look = await cue.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { opacity: parseFloat(cs.opacity), image: cs.backgroundImage, vis: cs.visibility };
    });
    expect(look.opacity).toBe(1);
    expect(look.vis).toBe("visible");
    expect(look.image).toContain("linear-gradient");
    // It must not eat taps: the point at its centre, on a body row, reaches the cell underneath.
    await detail.locator("tbody tr").nth(2).scrollIntoViewIfNeeded();
    const hit = await page.evaluate(() => {
      const cueEl = document.querySelector<HTMLElement>('[data-testid="more-columns-cue"]')!;
      const row = document.querySelectorAll<HTMLElement>('[data-testid="library-detail"] tbody tr')[2];
      const c = cueEl.getBoundingClientRect();
      const r = row.getBoundingClientRect();
      const el = document.elementFromPoint(c.x + c.width / 2, r.y + r.height / 2);
      return { tag: el?.tagName ?? null, inCue: !!el && cueEl.contains(el), inTable: !!el?.closest("table") };
    });
    expect(hit).toEqual({ tag: expect.stringMatching(/^(TD|TH|DIV)$/), inCue: false, inTable: true });
    await scrollTable(sc, "end");
    await expect(cue).toHaveCount(0);
    await scrollTable(sc, "start");
    await expect(cue).toBeVisible();
  });

  test("a table that fits shows no cue", async ({ page }) => {
    await page.goto("/");
    await page.getByPlaceholder(/Ask about your data/).fill("Top 5 selling SKUs by revenue");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    const answer = page.getByTestId("answer-message").last();
    const sc = answer.getByTestId("rows-scroller").first();
    await expect(sc).toBeVisible({ timeout: 20_000 });
    expect(await sc.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
    await expect(answer.getByTestId("more-columns-cue")).toHaveCount(0);
    await noPageOverflow(page);
  });

  test("pager has 44px targets, a rows label and a visible disabled state", async ({ page }) => {
    const data = await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    await expect(detail.getByText(`Rows 1 to ${data.rows.length} of ${data.row_count}`)).toBeVisible();
    for (const name of ["Prev", "Next"]) {
      const btn = detail.getByRole("button", { name, exact: true });
      const b = await box(btn);
      expect(b.height).toBeGreaterThanOrEqual(TARGET);
      expect(b.width).toBeGreaterThanOrEqual(TARGET);
      await expect(btn).toBeDisabled();
      expect(await btn.evaluate((el) => parseFloat(getComputedStyle(el).opacity))).toBeLessThan(0.7);
    }
  });

  test("table text is 12px or more and numbers are right-aligned and tabular", async ({ page }) => {
    const data = await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    // The whole block: the table and the pager row ("Rows a to b of n", Prev, Next).
    const block = detail.getByTestId("rows-block");
    expect(await smallText(block)).toEqual([]);
    for (const el of [block.locator("p").last(), block.getByRole("button", { name: "Next", exact: true })]) {
      expect(await el.evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(12);
    }
    const col = data.columns.indexOf("quantity_kg") + 1;
    for (const cell of [detail.locator("thead th").nth(col), detail.locator("tbody tr").first().locator("td").nth(col)]) {
      const s = await cell.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { align: cs.textAlign, num: cs.fontVariantNumeric };
      });
      expect(["right", "end"]).toContain(s.align);
      expect(s.num).toContain("tabular-nums");
    }
  });

  test("cells render the exact rows the API returned and the page never widens", async ({ page }) => {
    const data = await openTransactions(page);
    const body = page.getByTestId("library-detail").locator("tbody tr");
    await expect(body).toHaveCount(data.rows.length);
    for (const i of [0, data.rows.length - 1]) {
      const want = data.columns.map((c) => {
        const v = data.rows[i][c];
        return typeof v === "number" ? v.toLocaleString("en-MY", { maximumFractionDigits: 4 }) : String(v ?? "");
      });
      const cells = await body.nth(i).locator("td").allTextContents();
      expect(cells[0]).toBe(String(i + 1));
      expect(cells.slice(1)).toEqual(want);
    }
    await noPageOverflow(page);
    await scrollTable(scroller(page), "end");
    await noPageOverflow(page);
  });

  for (const theme of ["cream", "graphite"] as const) {
    test(`${theme}: the sticky column is opaque and matches the row it sits in`, async ({ page }) => {
      await setTheme(page, theme);
      await openTransactions(page);
      const detail = page.getByTestId("library-detail");
      const info = await detail.evaluate((root, fn) => {
        const rgba = eval(fn) as (c: string) => number[];
        const bg = (el: Element) => rgba(getComputedStyle(el).backgroundColor);
        const head = root.querySelector("thead")!;
        const th = root.querySelector("thead th:nth-child(2)")!;
        const rows = [...root.querySelectorAll("tbody tr")].slice(0, 4).map((tr) => ({
          tr: bg(tr),
          td: bg(tr.querySelector("td:nth-child(2)")!),
        }));
        return { head: bg(head), th: bg(th), rows };
      }, RGBA);
      // Header cell: opaque, the header's own colour.
      expect(info.th[3]).toBe(255);
      expect(info.th).toEqual(info.head);
      for (const r of info.rows) {
        expect(r.td[3], "sticky body cell must be opaque").toBe(255);
        expect(r.td, "sticky cell has the row's colour").toEqual(r.tr);
      }
      // The stripe is still there: neighbouring rows differ, and the sticky cell follows it.
      expect(info.rows[0].td).not.toEqual(info.rows[1].td);
      expect(info.rows[0].td).toEqual(info.rows[2].td);
      // Look at it: the sticky column while the table is scrolled.
      const sc = scroller(page);
      await scrollTable(sc, "end");
      await sc.scrollIntoViewIfNeeded();
      if (process.env.TABLES_SHOTS) await page.screenshot({ path: `${process.env.TABLES_SHOTS}/sticky-${theme}.png` });
    });
  }

  test("a 200-character first value cannot fill the screen: the sticky cell is capped", async ({ page }) => {
    const long = `SKU-${"ABCDEFGHIJ".repeat(20)}`;
    expect(long.length).toBe(204);
    await mockAsk(
      page,
      ["A", "B", "C"].map((k, i) => ({
        sku: i === 0 ? long : `SKU-${k}`,
        revenue_myr: 1000 * (i + 1),
        quantity_units: 10 * (i + 1),
        warehouse_region: "Klang Valley",
        last_updated_at: "2026-10-01",
      })),
    );
    const answer = await ask(page, "Top 5 selling SKUs by revenue");
    const sc = answer.getByTestId("rows-scroller").first();
    const table = sc.locator("table");
    const sticky = table.locator("tbody tr").first().locator("td").nth(1);
    const stickyHead = table.locator("thead th").nth(1);
    // Capped (45vw plus padding), not 900px, and the full value stays available on a long press.
    for (const cell of [sticky, stickyHead]) {
      expect((await box(cell)).width).toBeLessThanOrEqual(PHONE.width * 0.45 + 24);
    }
    await expect(sticky.locator("div")).toHaveAttribute("title", long);
    expect(await sticky.locator("div").evaluate((el) => getComputedStyle(el).textOverflow)).toBe("ellipsis");
    expect((await box(table)).width).toBeGreaterThan(PHONE.width);
    // Scrolled to the end, the last column is fully on screen, right of the sticky one.
    await scrollTable(sc, "end");
    await expect.poll(() => sc.evaluate((el) => el.scrollLeft)).toBeGreaterThan(50);
    const sb = await box(sticky);
    const last = await box(table.locator("tbody tr").first().locator("td").last());
    expect(last.x).toBeGreaterThanOrEqual(sb.x + sb.width - 0.5);
    expect(last.x + last.width).toBeLessThanOrEqual(PHONE.width + 0.5);
    // Never less than a column and a half of other content next to the sticky cell.
    expect(PHONE.width - (sb.x + sb.width)).toBeGreaterThan(120);
    await noPageOverflow(page);
  });

  test("Next and Prev page through the rows: label and first row follow", async ({ page }) => {
    await mockPreview(page);
    await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    const prev = detail.getByRole("button", { name: "Prev", exact: true });
    const next = detail.getByRole("button", { name: "Next", exact: true });
    const firstRow = () => detail.locator("tbody tr").first().locator("td");
    const expectPage = async (a: number, b: number) => {
      await expect(detail.getByText(rowsLabel(a, b, 35))).toBeVisible();
      await expect(detail.locator("tbody tr")).toHaveCount(b - a + 1);
      await expect(firstRow().nth(0)).toHaveText(String(a));
      await expect(firstRow().nth(1)).toHaveText(`TXN-${a}`);
    };
    await expectPage(1, 10);
    await expect(prev).toBeDisabled();
    await expect(next).toBeEnabled();
    // Enabled buttons are 44px targets too, not only disabled ones.
    for (const btn of [prev, next]) {
      const b = await box(btn);
      expect(b.width).toBeGreaterThanOrEqual(TARGET);
      expect(b.height).toBeGreaterThanOrEqual(TARGET);
    }
    await next.tap();
    await expectPage(11, 20);
    await expect(prev).toBeEnabled();
    await next.tap();
    await expectPage(21, 30);
    await next.tap();
    await expectPage(31, 35);
    await expect(next).toBeDisabled();
    await prev.tap();
    await expectPage(21, 30);
    await prev.tap();
    await expectPage(11, 20);
    await prev.tap();
    await expectPage(1, 10);
    await expect(prev).toBeDisabled();
  });

  test("pager targets stay 44px at the widest phone width (1023px)", async ({ page }) => {
    await page.setViewportSize({ width: 1023, height: 800 });
    await mockPreview(page);
    await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    await expect(detail.getByText(rowsLabel(1, 10, 35))).toBeVisible();
    for (const name of ["Prev", "Next"]) {
      const b = await box(detail.getByRole("button", { name, exact: true }));
      expect(b.width, name).toBeGreaterThanOrEqual(TARGET);
      expect(b.height, name).toBeGreaterThanOrEqual(TARGET);
    }
    expect(await smallText(detail.getByTestId("rows-block"))).toEqual([]);
  });

  test("the Sources grid: stepper targets are 44px, text is 12px or more, it never widens the page", async ({ page }) => {
    // The demo sources have no cell preview; point one at a fixture sheet so the grid renders.
    await page.route("**/v1/chat/ask", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.contributing_sources = (body.contributing_sources ?? []).map(
        (s: Record<string, unknown>, i: number) => (i === 0 ? { ...s, ref_id: "ref_erp_inv" } : s),
      );
      await route.fulfill({ response: res, json: body });
    });
    await ask(page, "Top 5 selling SKUs by revenue");
    await page.getByTestId("source-panel-open").tap();
    await page.getByRole("button", { name: /^▸/ }).first().tap();
    const next = page.getByRole("button", { name: "Next", exact: true });
    const prev = page.getByRole("button", { name: "Prev", exact: true });
    await expect(next).toBeVisible();
    const grid = next.locator('xpath=ancestor::div[contains(@class,"mt-2")][1]');
    await expect(grid.locator("tbody tr").first()).toBeVisible();
    for (const [name, btn] of [["Prev", prev], ["Next", next]] as const) {
      const b = await box(btn);
      expect(b.width, name).toBeGreaterThanOrEqual(TARGET);
      expect(b.height, name).toBeGreaterThanOrEqual(TARGET);
    }
    expect(await smallText(grid)).toEqual([]);
    // The stepper works: 1/6 -> 2/6 -> 1/6.
    const counter = grid.getByText(/^\d+\/\d+$/);
    await expect(counter).toHaveText("1/6");
    await next.tap();
    await expect(counter).toHaveText("2/6");
    await prev.tap();
    await expect(counter).toHaveText("1/6");
    await noPageOverflow(page);
    if (process.env.TABLES_SHOTS) await page.screenshot({ path: `${process.env.TABLES_SHOTS}/sources-grid.png` });
  });
});

test.describe("IPHONE-TABLES desktop", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("desktop table has no sticky column and no cue", async ({ page }) => {
    await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    const pos = await detail.evaluate((root) => {
      const cells = [...root.querySelectorAll<HTMLElement>("thead th, tbody td")];
      return [...new Set(cells.map((c) => getComputedStyle(c).position))];
    });
    expect(pos).toEqual(["static"]);
    await expect(detail.getByTestId("more-columns-cue")).toHaveCount(0);
    await expect(detail.getByText(/^Showing 1-15 of 15 rows/)).toBeVisible();
  });

  test("desktop pager pages through the rows and keeps its 11px text", async ({ page }) => {
    await mockPreview(page);
    await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    const next = detail.getByRole("button", { name: "Next", exact: true });
    const prev = detail.getByRole("button", { name: "Prev", exact: true });
    await expect(detail.getByText("Showing 1-10 of 35 rows")).toBeVisible();
    await next.click();
    await expect(detail.getByText("Showing 11-20 of 35 rows")).toBeVisible();
    await expect(detail.locator("tbody tr").first().locator("td").nth(1)).toHaveText("TXN-11");
    await prev.click();
    await expect(detail.getByText("Showing 1-10 of 35 rows")).toBeVisible();
    await expect(detail.locator("tbody tr").first().locator("td").nth(1)).toHaveText("TXN-1");
    // Desktop is unchanged: 11px pager text on the body's own line height.
    const p = detail.getByText(/^Showing 1-10/);
    expect(await p.evaluate((e) => getComputedStyle(e).fontSize)).toBe("11px");
    expect(await p.evaluate((e) => getComputedStyle(e).lineHeight)).toBe("16.5px");
  });
});
