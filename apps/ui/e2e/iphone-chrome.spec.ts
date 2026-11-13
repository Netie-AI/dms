import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * IPHONE-CHROME: the fixed chrome of Chat (top bar, banners, composer, tab bar)
 * must leave the thread room. The Space being asked and Sources live in the
 * composer row instead of a row of their own. Boxes only, no class strings.
 *
 * Measured before the change (non-thread share of the viewport, thread height):
 *   390x844: 31.5% (thread 578px; top bar 48, banner 50, composer 113, tab bar 55)
 *   375x667: 39.9% (thread 401px; same blocks)
 *   1280x800 (recorded from the live page): scope chip, dock open {104,676,768x38}, dock
 *   collapsed {262,676,768x38}; Sources tab {1244,82,36x718}; form {262,722,768x66}
 */

const SIZES = [
  { width: 390, height: 844, thread: 578, share: 0.26 },
  { width: 375, height: 667, thread: 401, share: 0.33 },
];
const TARGET = 44;

async function open(page: Page, mode: "cream" | "graphite") {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  if (mode === "graphite") {
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.getByRole("button", { name: "Switch to operator mode" })).toHaveCount(0);
  }
}

/** The active Space's name, read from Sources' accessible name ("Sources: Finance · 4 sources"). */
async function spaceName(page: Page) {
  const label = (await page.getByTestId("source-panel-open").getAttribute("aria-label")) ?? "";
  return label.replace(/^Sources: /, "").split(" · ")[0];
}

async function box(loc: Locator) {
  const b = await loc.boundingBox();
  expect(b).toBeTruthy();
  return b!;
}

/** Whether the corners and centre of a 44x44 box around the control's centre hit the control. */
async function effectiveHit(page: Page, testid: string) {
  return page.evaluate((id) => {
    const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const hits: boolean[] = [];
    for (const [dx, dy] of [[-21, -21], [21, -21], [-21, 21], [21, 21], [0, 0]]) {
      const hit = document.elementFromPoint(cx + dx, cy + dy);
      hits.push(!!hit && (hit === el || el.contains(hit)));
    }
    return hits;
  }, testid);
}

for (const size of SIZES) {
  for (const mode of ["cream", "graphite"] as const) {
    test.describe(`chat chrome ${size.width}x${size.height} ${mode}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, hasTouch: true, isMobile: true });

      test("the thread gets at least 40px more and the chrome share drops", async ({ page }) => {
        await open(page, mode);
        const thread = await box(page.getByTestId("chat-scroll"));
        expect(thread.height).toBeGreaterThanOrEqual(size.thread + 40);
        expect(1 - thread.height / size.height).toBeLessThanOrEqual(size.share);
      });

      test("the top bar names the Space while the composer is on screen; no chip in the composer", async ({ page }) => {
        await open(page, mode);
        const sw = page.locator("#space-switcher");
        await expect(sw).toBeVisible();
        const n = await box(sw);
        expect(n.y).toBeGreaterThanOrEqual(0);
        expect(n.x + n.width).toBeLessThanOrEqual(size.width);
        const space = await spaceName(page);
        expect(space.length).toBeGreaterThan(1);
        expect(await sw.evaluate((el: HTMLSelectElement) => el.selectedOptions[0].textContent)).toContain(space);
        const form = await box(page.locator("form"));
        expect(form.y + form.height).toBeLessThanOrEqual(size.height);
        await expect(page.getByTestId("scope-chip")).toHaveCount(0);
      });

      test("Sources shows the same count as the desktop chip and names the full scope", async ({ page, browser }) => {
        await open(page, mode);
        const desk = await browser.newContext({ viewport: { width: 1280, height: 800 } });
        const dp = await desk.newPage();
        await open(dp, mode);
        const label = (await dp.getByTestId("scope-chip").innerText()).trim();
        await desk.close();
        const count = label.match(/(\d+) sources?$/)![1];
        const btn = page.getByTestId("source-panel-open");
        await expect(btn.getByTestId("source-count")).toHaveText(count);
        expect(await btn.getAttribute("aria-label")).toBe(`Sources: ${label.replace(/^Asking: /, "")}`);
        expect(await btn.getByTestId("source-count").evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(12);
      });

      test("the field is wide, its placeholder fits, and 60 characters take at most 3 lines", async ({ page }) => {
        await open(page, mode);
        const field = page.getByPlaceholder(/Ask about your data/);
        const w = (await box(field)).width;
        expect(w).toBeGreaterThanOrEqual(size.width === 375 ? 220 : 230);
        const fit = await field.evaluate((el: HTMLTextAreaElement) => {
          const cs = getComputedStyle(el);
          const ctx = document.createElement("canvas").getContext("2d")!;
          ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
          const inner = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
          return { text: ctx.measureText(el.placeholder).width, inner };
        });
        expect(fit.text, `placeholder ${fit.text}px in ${fit.inner}px`).toBeLessThanOrEqual(fit.inner);
        const q = "What was total revenue by product category in Q3 last year?";
        expect(q.length).toBeGreaterThanOrEqual(58);
        await field.fill(q);
        const lines = await field.evaluate((el: HTMLTextAreaElement) => {
          const cs = getComputedStyle(el);
          const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
          const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
          return Math.round((el.getBoundingClientRect().height - pad) / lh);
        });
        console.log(`LINES ${size.width}x${size.height} ${mode}: ${lines}`);
        expect(lines).toBeLessThanOrEqual(3);
      });

      test("while an answer is pending the longer placeholder does not grow the field", async ({ page }) => {
        await open(page, mode);
        await page.route("**/api/v1/chat/ask", async (route) => {
          await new Promise((r) => setTimeout(r, 2500));
          await route.continue();
        });
        const field = page.getByPlaceholder(/Ask about your data/);
        await field.fill("What was total revenue?");
        await page.getByRole("button", { name: "Ask", exact: true }).click();
        const pending = page.getByPlaceholder(/Type next question/);
        await expect(pending).toBeVisible();
        expect((await box(pending)).height).toBeLessThanOrEqual(48);
      });

      test("Sources keeps a 44x44 hit area", async ({ page }) => {
        await open(page, mode);
        expect(await effectiveHit(page, "source-panel-open")).toEqual([true, true, true, true, true]);
        const b = await box(page.getByTestId("source-panel-open"));
        expect(b.height).toBeGreaterThanOrEqual(TARGET);
        expect(b.width).toBeGreaterThanOrEqual(TARGET);
      });

      test("nothing in the composer overlaps and the page does not scroll sideways", async ({ page }) => {
        await open(page, mode);
        const boxes = [
          await box(page.getByPlaceholder(/Ask about your data/)),
          await box(page.getByTestId("source-panel-open")),
          await box(page.getByRole("button", { name: "Ask", exact: true })),
        ];
        for (let i = 0; i < boxes.length; i++) {
          for (let j = i + 1; j < boxes.length; j++) {
            const a = boxes[i];
            const b = boxes[j];
            const apart =
              a.x + a.width <= b.x + 0.5 || b.x + b.width <= a.x + 0.5 ||
              a.y + a.height <= b.y + 0.5 || b.y + b.height <= a.y + 0.5;
            expect(apart, `controls ${i} and ${j} overlap`).toBe(true);
          }
        }
        // An ancestor may clip a too-wide row, so the document can look fine: check the boxes too.
        for (const b of boxes) expect(b.x + b.width).toBeLessThanOrEqual(size.width);
        const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(over).toBe(0);
        // The field is still usable: wide enough for a few words.
        expect(boxes[0].width).toBeGreaterThanOrEqual(200);
      });
    });
  }
}

test.describe("landscape phone 844x390", () => {
  test.use({ viewport: { width: 844, height: 390 }, hasTouch: true });

  test("one row: field, Sources, Ask; the top bar still names the Space", async ({ page }) => {
    await open(page, "cream");
    await expect(page.getByTestId("scope-chip")).toHaveCount(0);
    await expect(page.locator("#space-switcher")).toBeVisible();
    const f = await box(page.getByPlaceholder(/Ask about your data/));
    const s = await box(page.getByTestId("source-panel-open"));
    expect(Math.abs(s.y - f.y)).toBeLessThanOrEqual(2);
    expect(s.height).toBeGreaterThanOrEqual(TARGET);
    expect(f.height).toBeLessThanOrEqual(48);
    expect(f.width).toBeGreaterThanOrEqual(500);
    expect(await spaceName(page)).toBeTruthy();
  });
});

test.describe("desktop 1280x800", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("scope chip, Sources tab and composer sit exactly where they did before", async ({ page }) => {
    await open(page, "cream");
    await expect(page.getByTestId("scope-chip")).toHaveCount(1);
    expect(await box(page.getByTestId("scope-chip"))).toEqual({ x: 104, y: 676, width: 768, height: 38 });
    await page.getByRole("button", { name: "Collapse sources" }).click();
    expect(await box(page.getByTestId("scope-chip"))).toEqual({ x: 262, y: 676, width: 768, height: 38 });
    expect(await box(page.getByTestId("source-panel-open"))).toEqual({ x: 1244, y: 82, width: 36, height: 718 });
    expect(await box(page.locator("form"))).toEqual({ x: 262, y: 722, width: 768, height: 66 });
  });
});
