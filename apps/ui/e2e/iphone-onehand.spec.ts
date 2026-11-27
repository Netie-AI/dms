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
    // Two short source rows must not collapse the sheet to a sliver.
    expect(s.height).toBeGreaterThanOrEqual(PHONE.height * 0.4 - 1);
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

  test("a finished answer is shown from its top: badge and figure on screen", async ({ page }) => {
    await page.goto("/");
    const scrollerEl = page.getByTestId("chat-scroll");
    for (const q of ["What was total revenue?", "Top 5 selling SKUs by revenue"]) {
      const answer = await askQuestion(page, q);
      const text = answer.getByTestId("answer-text");
      // The thread smooth-scrolls; measure only once it has stopped moving.
      await scrollerEl.evaluate(
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
      const scroller = await box(scrollerEl);
      const a = await box(answer);
      const t = await box(text);
      // The answer's top is not above the screen, so the badge row is readable...
      expect(a.y).toBeGreaterThanOrEqual(scroller.y - 1);
      // ...and the sentence with the figure is fully on screen.
      expect(t.y).toBeGreaterThanOrEqual(scroller.y);
      expect(t.y + t.height).toBeLessThanOrEqual(scroller.y + scroller.height);
      await expect(text).toContainText(/\d/);
    }
  });

  test("tapping a figure opens its Sources", async ({ page }) => {
    await page.goto("/");
    const answer = await askQuestion(page, "What was total revenue?");
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
    await answer.getByTestId("answer-text").getByRole("button").first().click();
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByTestId("source-panel")).toContainText(/Tracing/);
  });

  test("the Sources sheet is a dialog: focus moves in, Escape closes, focus returns", async ({
    page,
  }) => {
    await page.goto("/");
    await askQuestion(page, "What was total revenue?");
    const opener = page.getByTestId("source-panel-open");
    await opener.click();
    const sheet = page.getByRole("dialog", { name: "Sources" });
    await expect(sheet).toBeVisible();
    await expect(page.getByRole("button", { name: "Collapse sources" })).toBeFocused();
    // What sits behind the sheet takes no focus.
    await expect(page.locator("main").first()).toHaveAttribute("inert", "");
    await expect(page.getByTestId("tab-bar")).toHaveAttribute("inert", "");
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    await expect(page.getByTestId("source-panel-open")).toBeFocused();
    await expect(page.locator("main").first()).not.toHaveAttribute("inert", "");
  });

  test("the drawer takes focus and gives it back to More", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    const more = page.getByRole("button", { name: "More pages" });
    await more.click();
    const drawer = page.getByTestId("left-nav");
    await expect(drawer.getByRole("link").first()).toBeFocused();
    await expect(page.locator("main").first()).toHaveAttribute("inert", "");
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(more).toBeFocused();
  });

  test("More is the active tab on a page that is not a tab", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    const more = page.getByRole("button", { name: "More pages" });
    const colour = () => more.evaluate((el) => getComputedStyle(el).borderTopColor);
    const idle = await colour();
    await go(page, "/runs");
    await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
    expect(await colour()).not.toBe(idle);
    await expect(page.getByTestId("tab-bar").locator('[aria-current="page"]')).toHaveCount(0);
    await go(page, "/studio");
    expect(await colour()).toBe(idle);
  });

  test("the New menu has 44px rows and closes on Escape and on an outside tap", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const open = page.getByRole("button", { name: "New", exact: true });
    const menu = page.getByTestId("topbar-new-menu");
    await open.click();
    for (const item of await menu.getByRole("button").all()) {
      expect((await box(item)).height).toBeGreaterThanOrEqual(TARGET);
    }
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await open.click();
    await expect(menu).toBeVisible();
    // Outside the menu, low on the screen (the menu hangs under the top bar).
    await page.touchscreen.tap(40, 560);
    await expect(menu).toHaveCount(0);
  });

  for (const theme of ["cream", "graphite"] as const) {
    test(`the demo banner is readable in ${theme}: 4.5:1 or better`, async ({ page }) => {
      // Force demo ask mode so the banner renders whatever mode the API under test is in.
      await page.route("**/api/health", async (route) => {
        const res = await route.fetch();
        await route.fulfill({ response: res, json: { ...(await res.json()), ask_mode: "demo" } });
      });
      await page.goto("/");
      if (theme === "graphite") {
        await page.getByRole("button", { name: "Switch to operator mode" }).click();
        await expect(page.locator("html")).toHaveAttribute("data-theme", "graphite");
      }
      const banner = page.getByRole("status").filter({ hasText: /DEMO ASK MODE/ });
      await expect(banner).toBeVisible();
      const ratio = await banner.evaluate((el) => {
        const lum = (c: string) => {
          const [r, g, b] = c.match(/[\d.]+/g)!.slice(0, 3).map((v) => {
            const x = Number(v) / 255;
            return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        const cs = getComputedStyle(el);
        const [hi, lo] = [lum(cs.color), lum(cs.backgroundColor)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
      });
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    });
  }

  test("an IME candidate confirmed with Enter does not send the question", async ({ page }) => {
    await page.goto("/");
    const field = page.getByPlaceholder(/Ask about your data/);
    await field.fill("zong shou ru");
    // Two separate signals: `isComposing` (the standard) and keyCode 229, which
    // iOS and older WebKit report while composing without setting isComposing.
    for (const init of [{ isComposing: true }, { keyCode: 229 }]) {
      await field.evaluate((el, extra) => {
        el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, ...extra }));
      }, init);
      await expect(field).toHaveValue("zong shou ru");
      await expect(page.getByTestId("answer-message")).toHaveCount(0);
    }
    await field.press("Enter");
    await expect(page.getByTestId("answer-message")).toHaveCount(1, { timeout: 20_000 });
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
    // The scope row is hidden to save height; Sources must still be one tap away.
    await expect(page.getByTestId("scope-chip")).toHaveCount(0);
    const sources = page.getByTestId("source-panel-open");
    await expect(sources).toBeVisible();
    expect((await box(sources)).height).toBeGreaterThanOrEqual(TARGET);
    await sources.click();
    const sheet = await box(page.getByTestId("source-panel"));
    expect(sheet.y).toBeGreaterThanOrEqual(0);
    await expect(page.getByRole("button", { name: "Collapse sources" })).toBeVisible();
    await ctx.close();
  });

  test("a small phone (375x667) shows suggestions without scrolling", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 667 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const scroller = await box(page.getByTestId("chat-scroll"));
    const visible = await page.getByTestId("chat-scroll").evaluate((el, bottom) => {
      return [...el.querySelectorAll("ul button")].filter((b) => b.getBoundingClientRect().bottom <= bottom).length;
    }, scroller.y + scroller.height);
    expect(visible).toBeGreaterThanOrEqual(2);
    await ctx.close();
  });

  for (const [width, phone] of [
    [1023, true],
    [1024, false],
    [1099, false],
  ] as const) {
    test(`at ${width}px the script breakpoint agrees with the stylesheet (${phone ? "phone" : "desktop"})`, async ({
      browser,
    }) => {
      // useIsLg() picks which nodes render; Tailwind's lg: classes pick how they look.
      // If the two disagree, a width gets a tab bar AND a desktop rail, or neither.
      const ctx = await browser.newContext({ viewport: { width, height: 800 } });
      const page = await ctx.newPage();
      await page.goto("/");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByTestId("tab-bar")).toHaveCount(phone ? 1 : 0);
      const hamburger = page.getByRole("button", { name: "Toggle sidebar" });
      if (phone) await expect(hamburger).toBeHidden();
      else await expect(hamburger).toBeVisible();
      await ctx.close();
    });
  }

  test("desktop has no tab bar", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByTestId("tab-bar")).toHaveCount(0);
    await ctx.close();
  });
});
