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
    const cue = page.getByTestId("library-detail").getByTestId("more-columns-cue");
    await expect(cue).toBeVisible();
    const cb = await box(cue);
    const sb = await box(sc);
    expect(Math.round(cb.x + cb.width)).toBe(Math.round(sb.x + sb.width));
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
    const small = await detail.evaluate((root) => {
      const out: string[] = [];
      const scope = root.querySelector('[data-testid="rows-table"]')!;
      for (const el of scope.querySelectorAll<HTMLElement>("th, td, p, button, span")) {
        if (!el.textContent?.trim()) continue;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs < 12) out.push(`${el.tagName} "${el.textContent.trim().slice(0, 20)}" ${fs}px`);
      }
      return out;
    });
    expect(small).toEqual([]);
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

  test("screenshots", async ({ page }) => {
    test.skip(!process.env.TABLES_SHOTS, "set TABLES_SHOTS=<dir> to capture");
    const dir = process.env.TABLES_SHOTS!;
    await openTransactions(page);
    const detail = page.getByTestId("library-detail");
    await detail.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${dir}/library-start.png` });
    await scrollTable(scroller(page), "end");
    await page.screenshot({ path: `${dir}/library-end.png` });
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
});
