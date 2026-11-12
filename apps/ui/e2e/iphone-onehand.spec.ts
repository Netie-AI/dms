import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * IPHONE-PWA-02: one-handed use at 390x844. Navigation and actions sit in the
 * thumb zone, every control is a 44px target, and an answer shows its figure
 * before its tools. All assertions are on rendered boxes, not class strings.
 */

const PHONE = { width: 390, height: 844 };
const TARGET = 44;

async function box(loc: Locator) {
  const b = await loc.boundingBox();
  expect(b).toBeTruthy();
  return b!;
}

async function go(page: Page, route: string) {
  await page.evaluate((r) => {
    history.pushState({}, "", r);
    dispatchEvent(new PopStateEvent("popstate"));
  }, route);
  await page.waitForLoadState("networkidle");
}

/** Visible controls under `root` whose tappable height is below 44px. */
async function smallTargets(page: Page, root: string): Promise<string[]> {
  return page.evaluate(
    ({ root, min }) => {
      const out: string[] = [];
      const sel = "button, select, summary, input:not([type=checkbox]):not([type=radio]):not([type=file]), a[href]";
      for (const el of document.querySelectorAll<HTMLElement>(`${root} :is(${sel})`)) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (getComputedStyle(el).visibility === "hidden") continue;
        // A control inside a sentence keeps its text size; its ::after pads the hit area.
        const pad = getComputedStyle(el, "::after");
        const extra =
          pad.content !== "none" && pad.position === "absolute"
            ? -parseFloat(pad.top || "0") - parseFloat(pad.bottom || "0")
            : 0;
        if (r.height + extra < min - 0.5) {
          out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 40)}" ${Math.round(r.height + extra)}px`);
        }
      }
      return out;
    },
    { root, min: TARGET },
  );
}

/** Visible text rendered under 12px. Uppercase micro-labels are the usual offenders. */
async function smallText(page: Page, root: string): Promise<string[]> {
  return page.evaluate((root) => {
    const out = new Set<string>();
    const scope = document.querySelector(root);
    if (!scope) return [];
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = (n.textContent || "").trim();
      const el = n.parentElement;
      if (!text || !el) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < 11.99) out.add(`${size}px "${text.slice(0, 30)}"`);
    }
    return [...out];
  }, root);
}

async function askQuestion(page: Page, q: string) {
  await page.getByPlaceholder(/Ask about your data/).fill(q);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = page.getByTestId("answer-message").last();
  await expect(answer).toBeVisible({ timeout: 20_000 });
  return answer;
}

test.describe("IPHONE-PWA-02 one-handed phone", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

  test("bottom tab bar holds the main pages in the thumb zone", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const bar = page.getByTestId("tab-bar");
    const b = await box(bar);
    expect(b.y).toBeGreaterThan(PHONE.height * (2 / 3));
    expect(b.y + b.height).toBeLessThanOrEqual(PHONE.height);
    const tabs = bar.getByRole("link");
    await expect(tabs).toHaveText(["Chat", "Library", "Spaces", "Trust"]);
    for (const tab of await tabs.all()) {
      const t = await box(tab);
      expect(t.height).toBeGreaterThanOrEqual(TARGET);
      expect(t.width).toBeGreaterThanOrEqual(TARGET);
    }
    await expect(bar.getByRole("link", { name: "Chat" })).toHaveAttribute("aria-current", "page");
    await bar.getByRole("link", { name: "Library" }).click();
    await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
    await expect(bar.getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
  });

  test("Operate mode tabs reach Studio and Ontology; More opens the rest", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    const bar = page.getByTestId("tab-bar");
    await expect(bar.getByRole("link")).toHaveText(["Chat", "Library", "Studio", "Ontology"]);
    await bar.getByRole("link", { name: "Studio" }).click();
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "More pages" }).click();
    const drawer = page.getByTestId("left-nav");
    await expect(drawer).toBeVisible();
    for (const link of await drawer.getByRole("link").all()) {
      expect((await box(link)).height).toBeGreaterThanOrEqual(TARGET);
    }
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  });

  test("an answer shows its figure first and its tools in one reachable strip", async ({ page }) => {
    await page.goto("/");
    const answer = await askQuestion(page, "What was total revenue?");
    const a = await box(answer);
    const text = answer.getByTestId("answer-text");
    await expect(text).toContainText(/\d/);
    const t = await box(text);
    // Badge row, then the sentence with the figure. No toolbar wall in between.
    expect(t.y - a.y).toBeLessThan(110);
    await expect(answer.getByRole("button", { name: /generated|certified|governed|no answer|unusual/ })).toBeVisible();

    const strip = answer.getByTestId("answer-actions");
    const s = await box(strip);
    expect(s.y).toBeGreaterThan(t.y + t.height - 1);
    expect(s.height).toBeLessThan(70);
    const overflow = await strip.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeGreaterThan(0);
    await strip.getByRole("button", { name: "Check accuracy" }).scrollIntoViewIfNeeded();
    await strip.getByRole("button", { name: "Check accuracy" }).click();
    await expect(answer.getByText(/Open Trust/)).toBeVisible();

    expect(await smallTargets(page, '[data-testid="answer-message"]')).toEqual([]);
  });

  test("Sources opens as a bottom sheet with its close control in reach", async ({ page }) => {
    await page.goto("/");
    await askQuestion(page, "What was total revenue?");
    await page.getByTestId("source-panel-open").click();
    const sheet = page.getByTestId("source-panel");
    const s = await box(sheet);
    expect(s.width).toBe(PHONE.width);
    expect(Math.round(s.y + s.height)).toBe(PHONE.height);
    expect(s.y).toBeGreaterThanOrEqual(PHONE.height * 0.2);
    const close = page.getByRole("button", { name: "Collapse sources" });
    const c = await box(close);
    expect(c.height).toBeGreaterThanOrEqual(TARGET);
    expect(c.y).toBeGreaterThanOrEqual(PHONE.height * 0.2);
    await close.click();
    await expect(sheet).toHaveCount(0);
  });

  test("the composer is one line until it needs more, and leaves the screen to the answer", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const field = page.getByPlaceholder(/Ask about your data/);
    const empty = await box(field);
    expect(empty.height).toBeLessThanOrEqual(48);
    expect(empty.height).toBeGreaterThanOrEqual(TARGET);
    await field.fill("one\ntwo\nthree\nfour");
    expect((await box(field)).height).toBeGreaterThan(empty.height + 40);
    await field.fill("");
    expect((await box(field)).height).toBeLessThanOrEqual(48);
    // Sending must not leave it tall: the "will queue" placeholder wraps while asking.
    await askQuestion(page, "What was total revenue?");
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeVisible();
    expect((await box(field)).height).toBeLessThanOrEqual(48);
    const main = await box(page.locator("main").first());
    const scroller = await box(page.getByTestId("chat-scroll"));
    expect(scroller.height / main.height).toBeGreaterThan(0.7);
  });

  test("follow-up questions are one swipeable row, not a stack", async ({ page }) => {
    await page.goto("/");
    await askQuestion(page, "What was total revenue?");
    const row = page.getByTestId("continue-chips");
    // One evaluate, one frame: the thread smooth-scrolls to the new answer, so
    // boxes read one call at a time land on different frames.
    const chips = await row.evaluate((el) =>
      [...el.querySelectorAll("button")].map((b) => {
        const r = b.getBoundingClientRect();
        return { y: Math.round(r.y), h: r.height };
      }),
    );
    expect(chips.length).toBeGreaterThan(1);
    expect(new Set(chips.map((c) => c.y)).size).toBe(1);
    for (const c of chips) expect(c.h).toBeGreaterThanOrEqual(TARGET);
    expect(await row.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeGreaterThan(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(1);
  });

  test("the phone top bar has no hamburger; the tab bar is the navigation", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("button", { name: "Toggle sidebar" })).toBeHidden();
    const space = await box(page.locator("#space-switcher"));
    expect(space.x).toBeLessThanOrEqual(12);
    expect(space.width).toBeGreaterThan(170);
  });

  for (const mode of ["ask", "operate"] as const) {
    test(`every control on every page is a 44px target (${mode})`, async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");
      if (mode === "operate") {
        await page.getByRole("button", { name: "Switch to operator mode" }).click();
      }
      const routes =
        mode === "ask"
          ? ["/", "/library", "/spaces", "/trust"]
          : ["/", "/library", "/spaces", "/studio", "/ontology", "/amend", "/audit", "/trust", "/runs"];
      const bad: Record<string, string[]> = {};
      for (const route of routes) {
        await go(page, route);
        const small = [...(await smallTargets(page, "main")), ...(await smallText(page, "#root"))];
        if (small.length) bad[route] = small;
      }
      expect(bad).toEqual({});
    });
  }
});

test.describe("IPHONE-PWA-03 install hint", () => {
  const IOS_SAFARI =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1";

  test("iOS Safari sees how to install, once; dismissing it sticks", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: PHONE, hasTouch: true, isMobile: true, userAgent: IOS_SAFARI });
    const page = await ctx.newPage();
    await page.goto("/");
    const hint = page.getByTestId("install-hint");
    await expect(hint).toBeVisible();
    await expect(hint).toContainText("Add to Home Screen");
    const dismiss = hint.getByRole("button", { name: "Dismiss install hint" });
    expect((await box(dismiss)).height).toBeGreaterThanOrEqual(TARGET);
    await dismiss.click();
    await expect(hint).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByTestId("install-hint")).toHaveCount(0);
    await ctx.close();
  });

  test("an installed app and a non-iOS browser never see it", async ({ browser }) => {
    const android = await browser.newContext({ viewport: PHONE, hasTouch: true, isMobile: true });
    const page = await android.newPage();
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByTestId("install-hint")).toHaveCount(0);
    await android.close();

    const installed = await browser.newContext({ viewport: PHONE, hasTouch: true, isMobile: true, userAgent: IOS_SAFARI });
    // iOS sets navigator.standalone when the page runs from the home screen.
    await installed.addInitScript(() => {
      Object.defineProperty(navigator, "standalone", { value: true });
    });
    const app = await installed.newPage();
    await app.goto("/");
    await expect(app.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(app.getByTestId("install-hint")).toHaveCount(0);
    await installed.close();
  });
});

test.describe("IPHONE-PWA-02 short and wide viewports", () => {
  test("landscape phone keeps most of the height for the conversation", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 844, height: 390 }, hasTouch: true });
    const page = await ctx.newPage();
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const scroller = await box(page.getByTestId("chat-scroll"));
    expect(scroller.height).toBeGreaterThan(390 * 0.45);
    await expect(page.getByTestId("tab-bar")).toBeVisible();
    await ctx.close();
  });

  test("desktop has no tab bar", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByTestId("tab-bar")).toHaveCount(0);
    await ctx.close();
  });
});
