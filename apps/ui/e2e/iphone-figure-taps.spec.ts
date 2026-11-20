import { expect, test, type Page } from "@playwright/test";

/**
 * IPHONE-TAP-01: a tap opens the provenance of the figure the person touched.
 *
 * On a phone every inline control in a sentence (.tap-pad) pads its hit area by 1rem above and
 * below. The answer's lines are closer together than that, so the padded areas of two figures on
 * adjacent wrapped lines overlap and the later one in the DOM wins the overlap, including the
 * lower edge of the upper figure's own digits: a tap on one number opened the Sources of another.
 * Every assertion here is on what the person reads after a real tap: the "Tracing <label>" line.
 */

const SIZES = [
  { width: 390, height: 844 },
  { width: 320, height: 568 },
];

/** Eight figures in one sentence, so some wrap onto adjacent lines above each other at any phone width. */
const FIGURES = [
  ["North", 1111.11],
  ["South", 2222.22],
  ["East", 3333.33],
  ["West", 4444.44],
  ["Central", 5555.55],
  ["Coast", 6666.66],
  ["Hill", 7777.77],
  ["Port", 8888.88],
] as const;
const fmt = (n: number) => `RM ${new Intl.NumberFormat("en-MY", { minimumFractionDigits: 2 }).format(n)}`;
const label = (region: string) => `${region} revenue`;

type Fig = { label: string; text: string; left: number; right: number; top: number; bottom: number };

async function ask(page: Page, size: { width: number; height: number }, mode: "cream" | "graphite" = "cream") {
  await page.route("**/v1/chat/ask", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.text = `Revenue was ${FIGURES.map(([r, v]) => `${fmt(v)} in ${r}`).join(", ")}.`;
    body.values = FIGURES.map(([r, v], i) => ({ ...(body.values?.[0] ?? {}), id: `v${i}`, label: label(r), value: v }));
    await route.fulfill({ response: res, json: body });
  });
  await page.setViewportSize(size);
  if (mode === "graphite") await page.addInitScript(() => localStorage.setItem("dms-theme", "graphite"));
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  // The placeholder is shortened at narrow widths: the composer is the page's one textarea.
  await page.locator("textarea").fill("What was total revenue?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const text = page.getByTestId("answer-message").last().getByTestId("answer-text");
  await expect(text.getByRole("button")).toHaveCount(FIGURES.length, { timeout: 20_000 });
  // The thread smooth-scrolls to the answer: measure only once it has stopped.
  await page.getByTestId("chat-scroll").evaluate(
    (el) =>
      new Promise<void>((resolve) => {
        let last = -1;
        let still = 0;
        const tick = () => {
          still = el.scrollTop === last ? still + 1 : 0;
          last = el.scrollTop;
          if (still >= 20) resolve();
          else requestAnimationFrame(tick);
        };
        tick();
      }),
  );
  return text;
}

/** The figures' boxes right now. `centre` scrolls that figure to the middle of the screen first (no animation). */
async function figures(page: Page, centre?: string): Promise<Fig[]> {
  const rects = await page
    .getByTestId("answer-message")
    .last()
    .getByTestId("answer-text")
    .evaluate((p, centre) => {
      const buttons = [...p.querySelectorAll("button")];
      buttons.find((b) => b.textContent === centre)?.scrollIntoView({ block: "center", behavior: "instant" });
      return buttons.map((b) => {
        const r = b.getBoundingClientRect();
        return { text: b.textContent ?? "", left: r.left, right: r.right, top: r.top, bottom: r.bottom };
      });
    }, centre ?? null);
  return rects.map((r, i) => ({ ...r, label: label(FIGURES[i][0]) }));
}

/** One stacked pair, measured again with the upper figure centred on screen. */
async function pairNow(page: Page, upperText: string, lowerText: string) {
  const figs = await figures(page, upperText);
  const upper = figs.find((f) => f.text === upperText)!;
  const lower = figs.find((f) => f.text === lowerText)!;
  return { upper, lower, x: Math.round((Math.max(upper.left, lower.left) + Math.min(upper.right, lower.right)) / 2) };
}

/** Pairs on adjacent lines whose boxes share a column: the upper one first. */
function stackedPairs(figs: Fig[]): { upper: Fig; lower: Fig; x: number }[] {
  const out: { upper: Fig; lower: Fig; x: number }[] = [];
  for (const upper of figs) {
    for (const lower of figs) {
      const gap = lower.top - upper.bottom;
      const left = Math.max(upper.left, lower.left);
      const right = Math.min(upper.right, lower.right);
      if (gap >= 0 && gap < 16 && right - left >= 12) out.push({ upper, lower, x: Math.round((left + right) / 2) });
    }
  }
  return out;
}

const dist = (f: Fig, y: number) => Math.max(f.top - y, 0, y - f.bottom);

/** A real tap at a point, then the label the Sources sheet says it is tracing. */
async function tapAndRead(page: Page, x: number, y: number): Promise<string> {
  const panel = page.getByTestId("source-panel");
  await expect(panel).toHaveCount(0);
  await page.touchscreen.tap(x, y);
  await expect(panel).toBeVisible();
  const traced = (await panel.locator("p", { hasText: /^Tracing/ }).locator("strong").textContent()) ?? "";
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  return traced;
}

for (const size of SIZES) {
  test.describe(`${size.width}x${size.height}`, () => {
    test.use({ viewport: size, hasTouch: true, isMobile: true });

    test("the sentence wraps with figures above each other, and their padded areas overlap", async ({ page }) => {
      await ask(page, size);
      const figs = await figures(page);
      expect(figs.map((f) => f.text)).toEqual(FIGURES.map(([, v]) => fmt(v)));
      const pairs = stackedPairs(figs);
      // Without a stacked pair the tests below would pass on nothing.
      expect(pairs.length, "no two figures on adjacent lines share a column").toBeGreaterThanOrEqual(1);
      // The raw hit areas do overlap (this is the CSS the tap rule has to arbitrate).
      const { upper, lower, x } = pairs[0];
      const owners = await page.evaluate(
        ({ x, from, to }) => {
          const out: string[] = [];
          for (let y = from; y <= to; y += 1) out.push(document.elementFromPoint(x, y)?.closest("button")?.textContent ?? "");
          return out;
        },
        { x, from: Math.ceil(upper.top) + 1, to: Math.floor(upper.bottom) - 1 },
      );
      expect(owners.filter((o) => o === lower.text).length, "rows of the upper figure's own box covered by the lower one's pad").toBeGreaterThan(0);
    });

    test("a tap anywhere between two stacked figures opens the nearer one (2px sweep, real taps)", async ({ page }) => {
      await ask(page, size);
      const first = stackedPairs(await figures(page))[0];
      const span = Math.floor(first.lower.bottom - first.upper.top);
      const wrong: string[] = [];
      let checked = 0;
      for (let dy = 1; dy <= span - 1; dy += 2) {
        const { upper, lower, x } = await pairNow(page, first.upper.text, first.lower.text);
        const y = Math.ceil(upper.top) + dy;
        const du = dist(upper, y);
        const dl = dist(lower, y);
        if (Math.abs(du - dl) < 1) continue; // the midpoint itself may go either way
        const want = du < dl ? upper.label : lower.label;
        const got = await tapAndRead(page, x, y);
        checked += 1;
        if (got !== want) wrong.push(`y=${y} (${du.toFixed(1)}px from upper, ${dl.toFixed(1)}px from lower): opened "${got}", nearest is "${want}"`);
      }
      expect(checked).toBeGreaterThanOrEqual(15);
      expect(wrong).toEqual([]);
    });

    test("every stacked pair: the lower edge of the upper figure's digits opens the upper figure", async ({ page }) => {
      await ask(page, size);
      const pairs = stackedPairs(await figures(page));
      for (const pair of pairs) {
        const { upper, lower, x } = await pairNow(page, pair.upper.text, pair.lower.text);
        expect(await tapAndRead(page, x, Math.floor(upper.bottom) - 2), `lower edge of ${upper.text}`).toBe(upper.label);
        expect(await tapAndRead(page, x, Math.ceil(lower.top) + 2), `upper edge of ${lower.text}`).toBe(lower.label);
      }
    });

    test("every figure opens its own Sources from its centre, and from the padded area where no other figure is nearer", async ({ page }) => {
      await ask(page, size);
      const names = (await figures(page)).map((f) => f.text);
      const at = async (text: string) => (await figures(page, text)).find((f) => f.text === text)!;
      for (const name of names) {
        const f = await at(name);
        const cx = Math.round((f.left + f.right) / 2);
        expect(await tapAndRead(page, cx, Math.round((f.top + f.bottom) / 2)), `centre of ${f.text}`).toBe(f.label);
      }
      // First and last line: nothing stacked on the outer side, so the full 1rem pad is the figure's.
      const first = await at(names[0]);
      expect(await tapAndRead(page, Math.round((first.left + first.right) / 2), Math.ceil(first.top) - 12)).toBe(first.label);
      const last = await at(names[names.length - 1]);
      expect(await tapAndRead(page, Math.round((last.left + last.right) / 2), Math.floor(last.bottom) + 12)).toBe(last.label);
    });

    test("the height that opens each figure: 44px where the lines allow, one line pitch where they do not", async ({ page }) => {
      await ask(page, size);
      const figs = await figures(page);
      // Per figure, the rows (at its centre column) whose nearest padded figure it is.
      const zones = figs.map((f) => {
        const cx = (f.left + f.right) / 2;
        const rivals = figs.filter((o) => o !== f && o.left - 4 <= cx && cx <= o.right + 4);
        let rows = 0;
        for (let y = Math.floor(f.top) - 16; y <= Math.ceil(f.bottom) + 16; y += 1) {
          const mine = dist(f, y);
          if (mine <= 16 && rivals.every((o) => dist(o, y) > 16 || dist(o, y) >= mine)) rows += 1;
        }
        return { text: f.text, rows, crowded: rivals.some((o) => dist(o, (f.top + f.bottom) / 2) < 16 + (f.bottom - f.top)) };
      });
      const pitch = Math.min(...stackedPairs(figs).map((p) => p.lower.top - p.upper.top));
      for (const z of zones) {
        if (!z.crowded) expect(z.rows, `${z.text} has no stacked neighbour`).toBeGreaterThanOrEqual(44);
        // A figure with a neighbour above and below keeps at least its own line.
        else expect(z.rows, `${z.text} is crowded`).toBeGreaterThanOrEqual(Math.floor(pitch) - 1);
      }
      expect(zones.some((z) => z.crowded)).toBe(true);
      // WCAG 2.2 target size (minimum) is 24px: even a crowded figure clears it.
      expect(Math.floor(pitch)).toBeGreaterThanOrEqual(24);
    });

    test("a keyboard press on a figure opens that figure (no pointer position to arbitrate)", async ({ page }) => {
      const text = await ask(page, size);
      const figs = await figures(page);
      const { upper } = stackedPairs(figs)[0];
      await text.getByRole("button", { name: upper.text }).focus();
      await page.keyboard.press("Enter");
      const panel = page.getByTestId("source-panel");
      await expect(panel.locator("p", { hasText: /^Tracing/ }).locator("strong")).toHaveText(upper.label);
    });
  });
}

test("graphite at 320: the lower edge of a stacked figure opens that figure", async ({ browser }) => {
  const context = await browser.newContext({ viewport: SIZES[1], hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await ask(page, SIZES[1], "graphite");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "graphite");
  const pair = stackedPairs(await figures(page))[0];
  const { upper, x } = await pairNow(page, pair.upper.text, pair.lower.text);
  expect(await tapAndRead(page, x, Math.floor(upper.bottom) - 2)).toBe(upper.label);
  await context.close();
});

test("at lg nothing is arbitrated: a click on a figure opens that figure, and the sentence text opens nothing", async ({ page }) => {
  await ask(page, { width: 1280, height: 800 });
  const traced = page.getByTestId("source-panel").locator("p", { hasText: /^Tracing/ }).locator("strong");
  // Desktop scrolls to the answer's foot: centre the figure before measuring it.
  const f = (await figures(page, fmt(FIGURES[2][1])))[2];
  await page.mouse.click((f.left + f.right) / 2, (f.top + f.bottom) / 2);
  await expect(traced).toHaveText(f.label);
  // 6px above a figure is plain sentence at lg (no pad there): the selection must not move.
  const other = (await figures(page, fmt(FIGURES[5][1])))[5];
  await page.mouse.click((other.left + other.right) / 2, other.top - 6);
  await page.waitForTimeout(200);
  await expect(traced).toHaveText(f.label);
  // And the figure itself still answers.
  await page.mouse.click((other.left + other.right) / 2, (other.top + other.bottom) / 2);
  await expect(traced).toHaveText(other.label);
});
