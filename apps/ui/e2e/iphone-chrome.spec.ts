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
async function effectiveHit(page: Page, selector: string) {
  return page.evaluate((sel) => {
    const el = document.querySelector<HTMLElement>(sel)!;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const hits: boolean[] = [];
    for (const [dx, dy] of [[-21, -21], [21, -21], [-21, 21], [21, 21], [0, 0]]) {
      const hit = document.elementFromPoint(cx + dx, cy + dy);
      hits.push(!!hit && (hit === el || el.contains(hit)));
    }
    return hits;
  }, selector);
}

for (const size of SIZES) {
  for (const mode of ["cream", "graphite"] as const) {
    test.describe(`chat chrome ${size.width}x${size.height} ${mode}`, () => {
      test.use({ viewport: { width: size.width, height: size.height }, hasTouch: true, isMobile: true });

      test("the thread gets at least 40px more and the chrome share drops", async ({ page }) => {
        await open(page, mode);
        // Independent of any banner: the composer block was 113px tall, one row is 61.
        const composer = await box(page.getByTestId("chat-scroll").locator("xpath=following-sibling::*[1]"));
        expect(composer.height).toBeLessThanOrEqual(64);
        // The thread numbers below assume the 50px demo banner is showing (the demo API is up);
        // without it the thread is taller and they pass trivially, hence the check above.
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

      test("Sources keeps a 44x44 hit area", async ({ page }) => {
        await open(page, mode);
        expect(await effectiveHit(page, '[data-testid="source-panel-open"]')).toEqual([true, true, true, true, true]);
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

for (const vp of [{ width: 844, height: 390 }, { width: 667, height: 375 }]) {
  for (const mode of ["cream", "graphite"] as const) {
    test.describe(`landscape phone ${vp.width}x${vp.height} ${mode}`, () => {
      test.use({ viewport: vp, hasTouch: true });

      test("one row: field, Sources, Ask; the top bar still names the Space", async ({ page }) => {
        await open(page, mode);
        await expect(page.getByTestId("scope-chip")).toHaveCount(0);
        await expect(page.locator("#space-switcher")).toBeVisible();
        const f = await box(page.locator("form textarea"));
        const s = await box(page.getByTestId("source-panel-open"));
        const a = await box(page.getByRole("button", { name: "Ask", exact: true }));
        expect(Math.abs(s.y - f.y)).toBeLessThanOrEqual(2);
        expect(Math.abs(a.y + a.height - (f.y + f.height))).toBeLessThanOrEqual(2);
        expect(s.height).toBeGreaterThanOrEqual(TARGET);
        expect(f.height).toBeLessThanOrEqual(48);
        expect(f.width).toBeGreaterThanOrEqual(400);
        expect(f.x + f.width).toBeLessThanOrEqual(s.x);
        expect(s.x + s.width).toBeLessThanOrEqual(a.x);
        expect(await spaceName(page)).toBeTruthy();
      });
    });
  }
}

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

for (const width of [1280, 1024]) {
  test.describe(`desktop ${width}`, () => {
    test.use({ viewport: { width, height: 800 } });

    test("Sources keeps its desktop name and word", async ({ page }) => {
      await open(page, "cream");
      const collapse = page.getByRole("button", { name: "Collapse sources" });
      if (await collapse.count()) await collapse.click();
      const btn = page.getByRole("button", { name: "Open sources", exact: true });
      await expect(btn).toBeVisible();
      await expect(btn).toHaveAttribute("data-testid", "source-panel-open");
      await expect(btn).toHaveText("Sources");
      expect(await btn.getAttribute("aria-label")).toBe("Open sources");
      await expect(page.getByTestId("source-count")).toHaveCount(0);
    });
  });
}

/** Spaces without a source_count, so the count is unknown until the library list answers. */
async function stubSpacesWithoutCount(page: Page) {
  await page.route(
    (url) => url.pathname === "/api/v1/spaces",
    async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({
        response: res,
        json: { ...body, spaces: body.spaces.map(({ source_count: _drop, ...rest }: Record<string, unknown>) => rest) },
      });
    },
  );
}

/** A stubbed library list of `n` rows for any Space, optionally delayed per Space id. */
async function stubSources(page: Page, counts: Record<string, number>, delayMs: Record<string, number> = {}) {
  await page.route("**/api/v1/library/sources**", async (route) => {
    const id = new URL(route.request().url()).searchParams.get("space_id") ?? "";
    const n = counts[id] ?? counts["*"] ?? 0;
    if (delayMs[id]) await new Promise((r) => setTimeout(r, delayMs[id]));
    await route.fulfill({
      json: Array.from({ length: n }, (_, i) => ({
        id: `src_${i}`, kind: "csv", ref: `x/${i}.csv`, scope: "team", space_id: id, space_name: "x",
      })),
    });
  });
}

const FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const WAREHOUSE = "dddddddd-dddd-dddd-dddd-dddddddddddd";

const FIT_WIDTHS = [
  { width: 320, height: 568 },
  { width: 360, height: 640 },
  { width: 375, height: 667 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
];

async function fit(field: Locator) {
  return field.evaluate((el: HTMLTextAreaElement) => {
    const cs = getComputedStyle(el);
    const ctx = document.createElement("canvas").getContext("2d")!;
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const inner = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    return { text: ctx.measureText(el.placeholder).width, inner, placeholder: el.placeholder };
  });
}

for (const vp of FIT_WIDTHS) {
  for (const mode of ["cream", "graphite"] as const) {
    test.describe(`composer ${vp.width}x${vp.height} ${mode}`, () => {
      test.use({ viewport: vp, hasTouch: true, isMobile: true });

      test("idle and pending placeholders fit on one line; the field keeps a name", async ({ page }) => {
        await open(page, mode);
        const field = page.locator("form textarea");
        const idle = await fit(field);
        expect(idle.text + 4, `idle "${idle.placeholder}" ${idle.text}px in ${idle.inner}px`).toBeLessThanOrEqual(idle.inner);
        if (vp.width >= 360) expect(idle.placeholder).toBe("Ask about your data…");
        await expect(page.getByRole("textbox", { name: "Ask about your data" })).toHaveCount(1);
        await page.route("**/api/v1/chat/ask", async (route) => {
          await new Promise((r) => setTimeout(r, 2500));
          await route.continue();
        });
        await field.fill("What was total revenue?");
        await page.getByRole("button", { name: "Ask", exact: true }).click();
        await expect(page.getByRole("button", { name: "Queue", exact: true })).toBeVisible();
        const pending = await fit(field);
        expect(pending.placeholder).not.toBe(idle.placeholder);
        expect(pending.text + 4, `pending "${pending.placeholder}" ${pending.text}px in ${pending.inner}px`).toBeLessThanOrEqual(pending.inner);
        expect((await box(field)).height).toBeLessThanOrEqual(48);
      });

      test("all three controls keep a 44x44 hit area", async ({ page }) => {
        await open(page, mode);
        for (const sel of ['form textarea', '[data-testid="source-panel-open"]', 'form button[type="submit"]']) {
          // Ask is disabled while the field is empty; elementFromPoint still reports it.
          expect(await effectiveHit(page, sel), sel).toEqual([true, true, true, true, true]);
          const b = await box(page.locator(sel));
          expect(b.height, sel).toBeGreaterThanOrEqual(TARGET);
          expect(b.width, sel).toBeGreaterThanOrEqual(TARGET);
        }
      });

      test("order is field, Sources, Ask by position and by Tab; the icon is 20px or more", async ({ page }) => {
        await open(page, mode);
        const f = await box(page.locator("form textarea"));
        const s = await box(page.getByTestId("source-panel-open"));
        const a = await box(page.getByRole("button", { name: "Ask", exact: true }));
        expect(f.x + f.width).toBeLessThanOrEqual(s.x);
        expect(s.x + s.width).toBeLessThanOrEqual(a.x);
        await page.locator("form textarea").fill("hello");
        await page.locator("form textarea").focus();
        await page.keyboard.press("Tab");
        await expect(page.getByTestId("source-panel-open")).toBeFocused();
        await page.keyboard.press("Tab");
        await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeFocused();
        const icon = await box(page.getByTestId("source-panel-open").locator("svg"));
        expect(icon.width).toBeGreaterThanOrEqual(20);
        expect(icon.height).toBeGreaterThanOrEqual(20);
      });

      test("the Sources control does not change width when the count arrives", async ({ page }) => {
        await stubSpacesWithoutCount(page);
        await stubSources(page, { "*": 2 }, { [FINANCE]: 1500 });
        await page.goto("/");
        await expect(page.locator("#space-switcher")).toBeVisible();
        await page.evaluate(() => document.fonts.ready); // a late web font would move Ask by 2px
        const btn = page.getByTestId("source-panel-open");
        const before = await box(btn);
        const fieldBefore = await box(page.locator("form textarea"));
        await expect(btn.getByTestId("source-count")).toBeVisible({ timeout: 10_000 });
        const after = await box(btn);
        expect(Math.abs(after.width - before.width)).toBeLessThan(0.5);
        expect(Math.abs((await box(page.locator("form textarea"))).width - fieldBefore.width)).toBeLessThan(0.5);
      });

      test("an unknown count shows no number, and a 3-digit count fits", async ({ page }) => {
        await stubSpacesWithoutCount(page);
        await page.route("**/api/v1/library/sources**", (route) => route.abort()); // the count stays unknown
        await page.goto("/");
        await expect(page.locator("#space-switcher")).toBeVisible();
        const btn = page.getByTestId("source-panel-open");
        await expect(btn).toBeVisible();
        // The fixture Space reads 3 until the stubbed Spaces list (no counts) lands.
        await expect(btn).toHaveAttribute("aria-label", "Sources: Finance");
        await expect(btn.getByTestId("source-count")).toHaveCount(0);
        expect(await btn.innerText()).not.toMatch(/\d|null|NaN/);
      });

      test("a 3-digit count: nothing overlaps and all three controls stay on screen", async ({ page }) => {
        await stubSources(page, { "*": 120 });
        await open(page, mode);
        await expect(page.getByTestId("source-count")).toHaveText("120");
        const boxes = [
          await box(page.locator("form textarea")),
          await box(page.getByTestId("source-panel-open")),
          await box(page.getByRole("button", { name: "Ask", exact: true })),
        ];
        for (let i = 0; i < boxes.length; i++) {
          expect(boxes[i].x + boxes[i].width).toBeLessThanOrEqual(vp.width);
          for (let j = i + 1; j < boxes.length; j++) {
            const a = boxes[i];
            const b = boxes[j];
            expect(a.x + a.width <= b.x + 0.5 || b.x + b.width <= a.x + 0.5, `${i} overlaps ${j}`).toBe(true);
          }
        }
        expect(boxes[0].width).toBeGreaterThanOrEqual(130);
      });
    });
  }
}

test.describe("Sources sheet open", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the Sources control is not rendered while the sheet is open", async ({ page }) => {
    await open(page, "cream");
    await page.getByTestId("source-panel-open").click();
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByTestId("source-panel-open")).toHaveCount(0);
    await page.getByRole("button", { name: "Collapse sources" }).click();
    await expect(page.getByTestId("source-panel-open")).toBeVisible();
  });
});

for (const [label, vp] of [["phone", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 800 }]] as const) {
  test.describe(`switching Space, ${label}`, () => {
    test.use({ viewport: vp, hasTouch: label === "phone", isMobile: label === "phone" });

    test("the old Space's count is never shown under the new Space's name", async ({ page }) => {
      await stubSources(page, { [FINANCE]: 3, [WAREHOUSE]: 2 }, { [WAREHOUSE]: 2500 });
      await open(page, "cream");
      await page.evaluate(() => {
        const seen: string[] = [];
        (window as unknown as { __seen: string[] }).__seen = seen;
        const read = () => {
          const b = document.querySelector('[data-testid="source-panel-open"]');
          const c = document.querySelector('[data-testid="scope-chip"]');
          if (b) seen.push(b.getAttribute("aria-label") ?? "");
          if (c) seen.push(c.textContent ?? "");
        };
        new MutationObserver(read).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      });
      const collapse = page.getByRole("button", { name: "Collapse sources" });
      if (label === "desktop" && (await collapse.count())) await collapse.click();
      await page.locator("#space-switcher").selectOption(WAREHOUSE);
      await page.waitForTimeout(800);
      const during = await page.evaluate(() => (window as unknown as { __seen: string[] }).__seen);
      expect(during.join("\n")).toContain("Warehouse Ops");
      expect(during.filter((t) => t.includes("Warehouse Ops") && /\b3 sources/.test(t))).toEqual([]);
      await page.waitForTimeout(2500);
      const last = label === "phone"
        ? await page.getByTestId("source-panel-open").getAttribute("aria-label")
        : await page.getByTestId("scope-chip").innerText();
      expect(last).toContain("Warehouse Ops · 2 sources");
    });
  });
}
