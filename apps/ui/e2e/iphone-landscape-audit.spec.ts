import { expect, test, type Page } from "@playwright/test";

/**
 * IPHONE-AUDIT-13: four findings from the round 13 measured phone audit, each asserted on what a
 * person sees.
 *  F1 Library: in landscape the table tree was an 8px sliver (the 44px filter ate it), so no table
 *     could be reached at all. A person must see at least two whole rows of the tree.
 *  F2 Chat in landscape: fixed chrome (top bar, demo banner, composer, tab bar) took 48% of 844x390 and 54% of 667x375.
 *     The conversation must keep at least 54% of the height (chrome 46% or less). The demo banner stays whole (rule 11).
 *  F4 Two placeholders were cut off at 320px ("Supplier audit, Proc").
 *  F5 "Session ses_471a2d2604e046e2" is noise on a phone and pushed the useful words out of the line.
 * Desktop (lg and up) is pinned by desktop-unchanged.spec.ts; the lg checks here guard that too.
 */

const MODES = ["cream", "graphite"] as const;
type Mode = (typeof MODES)[number];

async function open(page: Page, path: string, mode: Mode) {
  await page.addInitScript((m) => localStorage.setItem("dms-theme", m), mode);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
}

// 844x390 (iPhone 13/14) and 667x375 (SE, 8) are the landscape phones in use. 568x320 (SE 1st gen)
// is a floor that must not get worse: the demo banner wraps to two lines there and the three bars
// alone take 132px of 320.
const SIZES_LANDSCAPE = [
  { width: 844, height: 390, share: 0.54, rows: 2 },
  { width: 667, height: 375, share: 0.54, rows: 2 },
  { width: 568, height: 320, share: 0.42, rows: 1 },
];
const SIZES_PORTRAIT = [
  { width: 390, height: 844, share: 0, rows: 2 },
  { width: 320, height: 568, share: 0, rows: 2 },
];

for (const mode of MODES) {
  for (const vp of [...SIZES_LANDSCAPE, ...SIZES_PORTRAIT]) {
    test(`F1 ${mode} ${vp.width}x${vp.height}: the Library tree shows at least ${vp.rows} whole row(s)`, async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: vp, hasTouch: true, isMobile: true });
      const page = await ctx.newPage();
      await open(page, "/library", mode);
      const scroller = page.getByTestId("library-tree").locator("div.overflow-y-auto");
      await expect(scroller.locator("button").first()).toBeVisible();
      const s = (await scroller.boundingBox())!;
      const rows = await scroller.locator("button").evaluateAll((els, n) =>
        els.slice(0, n).map((e) => {
          const r = e.getBoundingClientRect();
          return { top: r.top, bottom: r.bottom };
        }),
        vp.rows,
      );
      expect(rows.length).toBe(vp.rows);
      for (const r of rows) {
        expect(r.top).toBeGreaterThanOrEqual(s.y - 1);
        expect(r.bottom).toBeLessThanOrEqual(s.y + s.height + 1);
      }
      // The page itself must not scroll sideways, and the tree stays on screen.
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(vp.width);
      expect(s.y + s.height).toBeLessThanOrEqual(vp.height);
      await ctx.close();
    });
  }

  for (const vp of SIZES_LANDSCAPE) {
    test(`F2 ${mode} ${vp.width}x${vp.height}: Chat keeps ${Math.round(vp.share * 100)}% of the height for the conversation, banner whole`, async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: vp, hasTouch: true, isMobile: true });
      const page = await ctx.newPage();
      await open(page, "/", mode);
      const scroller = (await page.getByTestId("chat-scroll").boundingBox())!;
      const banner = page.getByRole("status").filter({ hasText: "DEMO ASK MODE" });
      await expect(banner).toBeVisible();
      const b = (await banner.boundingBox())!;
      expect(b.y).toBeGreaterThanOrEqual(0);
      // The banner's whole sentence is on screen: not clipped, not truncated.
      expect(await banner.evaluate((e) => e.scrollHeight <= e.clientHeight + 1 && e.scrollWidth <= e.clientWidth + 1)).toBe(true);
      expect(await banner.evaluate((e) => getComputedStyle(e).textOverflow)).not.toBe("ellipsis");
      expect(scroller.height / vp.height).toBeGreaterThanOrEqual(vp.share);
      await ctx.close();
    });
  }

  test(`F4 ${mode} 320x568: no placeholder is cut off`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 320, height: 568 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const clipped: string[] = [];
    // Studio is Operate mode only (graphite): Ask mode has no such page.
    for (const path of mode === "graphite" ? ["/", "/library", "/spaces", "/studio"] : ["/", "/library", "/spaces"]) {
      await open(page, path, mode);
      if (path === "/studio") await expect(page.getByTestId("sql-source-tables")).toBeAttached();
      const found = await page.evaluate(() => {
        const out: string[] = [];
        for (const el of document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input[placeholder], textarea[placeholder]")) {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          const cs = getComputedStyle(el);
          const ph = getComputedStyle(el, "::placeholder");
          const probe = document.createElement("span");
          probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${cs.font};letter-spacing:${cs.letterSpacing}`;
          probe.textContent = el.placeholder;
          document.body.appendChild(probe);
          const need = probe.getBoundingClientRect().width;
          probe.remove();
          const content = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
          // A textarea wraps its placeholder: it is cut only if the wrapped text is taller than the box.
          if (el instanceof HTMLTextAreaElement) {
            const lines = Math.ceil(need / content);
            const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4;
            const h = el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
            if (lines * lh > h + 1) out.push(`${el.placeholder} (${lines} lines in ${h}px)`);
          } else if (need > content + 1) out.push(`${el.placeholder} (${Math.round(need)}px in ${Math.round(content)}px)`);
          void ph;
        }
        return out;
      });
      clipped.push(...found.map((f) => `${path}: ${f}`));
    }
    expect(clipped).toEqual([]);
    await ctx.close();
  });

  test(`F5 ${mode} 390x844: the thread header drops the session id and keeps the words that help`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    await open(page, "/", mode);
    await page.locator("textarea").fill("What was total revenue?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("answer-message")).toHaveCount(1, { timeout: 20_000 });
    const body = await page.getByTestId("chat-scroll").innerText();
    expect(body).not.toMatch(/ses_[0-9a-f]{8}/);
    expect(body).toContain("demo warehouse");
    await ctx.close();
  });
}

test("F5 lg: desktop still shows the session id", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await open(page, "/", "cream");
  await page.locator("textarea").fill("What was total revenue?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("answer-message")).toHaveCount(1, { timeout: 20_000 });
  expect(await page.getByTestId("chat-scroll").innerText()).toMatch(/Session ses_[0-9a-f]{16} · demo warehouse/);
});
