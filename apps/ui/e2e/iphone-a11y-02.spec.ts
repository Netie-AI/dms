import { expect, test, type Page } from "@playwright/test";

/**
 * IPHONE-A11Y-02 (round 7 of the PR #510 checker).
 *
 *  A. Crossing lg never renders the Sources sheet, not even for one commit. Sampled every frame
 *     and on every attribute mutation, not only at the end state.
 *  B. The phone nav drawer is a div role=dialog around a <nav aria-label="Primary">; at lg the nav
 *     is the docked landmark alone.
 *  C. The guards behind "Show me why", the Sources sheet and the focus hand-back, each pinned by a
 *     test that goes red when its line is removed.
 */

const PHONE = { width: 390, height: 844 };
const ASK = /Ask about your data/;

async function askQuestion(page: Page) {
  await page.getByPlaceholder(ASK).fill("What was total revenue?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("answer-message").last()).toBeVisible({ timeout: 20_000 });
}

// ---------------------------------------------------------------------------------------------
// A. no transient sheet when the window crosses lg
// ---------------------------------------------------------------------------------------------

type Sampled = {
  frames: { t: number; dialogs: number; modal: number; inert: number; composerFocused: boolean }[];
  mutations: string[];
  composerBlurs: string[];
};

/** Start recording: one row per animation frame, plus every role/aria-modal/inert change and every composer blur. */
async function startSampler(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __s: Sampled & { stop: () => void } };
    const frames: Sampled["frames"] = [];
    const mutations: string[] = [];
    const composerBlurs: string[] = [];
    const composer = () => document.querySelector("textarea");
    const describe = (el: Element) =>
      `${el.tagName.toLowerCase()}${el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : ""}`;
    const mo = new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "attributes") {
          const v = (r.target as Element).getAttribute(r.attributeName!);
          // Only the "turned on" side: role=dialog, aria-modal=true, inert present.
          if (
            (r.attributeName === "role" && v === "dialog") ||
            (r.attributeName === "aria-modal" && v === "true") ||
            (r.attributeName === "inert" && v !== null)
          ) {
            mutations.push(`${describe(r.target as Element)} ${r.attributeName}=${v}`);
          }
        } else {
          for (const n of r.addedNodes) {
            if (!(n instanceof Element)) continue;
            const hit = n.matches('[role="dialog"], [aria-modal="true"], [inert]')
              ? n
              : n.querySelector('[role="dialog"], [aria-modal="true"], [inert]');
            if (hit) mutations.push(`added ${describe(hit)}`);
          }
        }
      }
    });
    mo.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["role", "aria-modal", "inert"],
    });
    const onOut = (e: FocusEvent) => {
      if (e.target === composer()) composerBlurs.push(`blur -> ${describe((e.relatedTarget as Element) ?? document.body)}`);
    };
    document.addEventListener("focusout", onOut, true);
    let live = true;
    const tick = () => {
      if (!live) return;
      frames.push({
        t: performance.now(),
        dialogs: document.querySelectorAll('[role="dialog"]').length,
        modal: document.querySelectorAll('[aria-modal="true"]').length,
        inert: document.querySelectorAll("[inert]").length,
        composerFocused: document.activeElement === composer() && composer() !== null,
      });
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    w.__s = {
      frames,
      mutations,
      composerBlurs,
      stop() {
        live = false;
        mo.disconnect();
        document.removeEventListener("focusout", onOut, true);
      },
    };
  });
}

async function stopSampler(page: Page): Promise<Sampled> {
  return page.evaluate(() => {
    const w = window as unknown as { __s: Sampled & { stop: () => void } };
    w.__s.stop();
    const { frames, mutations, composerBlurs } = w.__s;
    return { frames, mutations, composerBlurs };
  });
}

/** Resize while sampling, with a draft in the focused composer; wait a few frames either side. */
async function resizeSampled(
  page: Page,
  from: { width: number; height: number },
  to: { width: number; height: number },
  mode: "cream" | "graphite" = "cream",
) {
  await page.setViewportSize(from);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  if (mode === "graphite") await page.getByRole("button", { name: "Switch to operator mode" }).click();
  await expect(page.getByTestId("source-panel")).toBeVisible();
  const composer = page.getByPlaceholder(ASK);
  await composer.click();
  await composer.fill("revenue by region next quarter");
  await expect(composer).toBeFocused();

  await startSampler(page);
  const frames = () => page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));
  await frames();
  await page.setViewportSize(to);
  for (let i = 0; i < 6; i++) await frames();
  const s = await stopSampler(page);
  return s;
}

for (const [from, to] of [
  [{ width: 1280, height: 800 }, { width: 800, height: 800 }],
  [{ width: 1024, height: 800 }, { width: 390, height: 844 }],
] as const) {
  test(`A: ${from.width} to ${to.width}, no frame renders a dialog or inert, and the composer keeps focus`, async ({ page }) => {
    const s = await resizeSampled(page, from, to);
    expect(s.frames.length, "the sampler saw frames").toBeGreaterThan(4);
    // Every frame.
    for (const f of s.frames) {
      expect(f, JSON.stringify(f)).toMatchObject({ dialogs: 0, modal: 0, inert: 0, composerFocused: true });
    }
    // Every commit, including one that is reverted before the next frame.
    expect(s.mutations, "role=dialog / aria-modal / inert was switched on at some point").toEqual([]);
    expect(s.composerBlurs, "the composer lost focus at some point").toEqual([]);
    // And the end state: draft kept, sheet closed, phone layout.
    await expect(page.getByRole("textbox", { name: "Ask about your data" })).toHaveValue("revenue by region next quarter");
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
  });
}

for (const [from, to] of [
  [{ width: 1280, height: 800 }, { width: 800, height: 800 }],
  [{ width: 1024, height: 800 }, { width: 390, height: 844 }],
] as const) {
  test(`A (operate mode, nav docked open): ${from.width} to ${to.width}, no frame renders the nav as a dialog`, async ({ page }) => {
    const s = await resizeSampled(page, from, to, "graphite");
    for (const f of s.frames) {
      expect(f, JSON.stringify(f)).toMatchObject({ dialogs: 0, modal: 0, inert: 0, composerFocused: true });
    }
    expect(s.mutations).toEqual([]);
    expect(s.composerBlurs).toEqual([]);
  });
}

test("A: the other way (800 to 1280) is also clean, and the dock is open on arrival", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 800 });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const composer = page.getByRole("textbox", { name: "Ask about your data" });
  await composer.click();
  await composer.fill("a draft");
  await startSampler(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByTestId("source-panel")).toBeVisible();
  const s = await stopSampler(page);
  for (const f of s.frames) expect(f, JSON.stringify(f)).toMatchObject({ dialogs: 0, modal: 0, inert: 0 });
  expect(s.mutations).toEqual([]);
  await expect(page.getByPlaceholder(ASK)).toHaveValue("a draft");
});

test("A: crossing lg never opens the sheet, and the dock comes back as the user left it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  // 1. A dock the user collapsed stays collapsed after a round trip through the phone width.
  await expect(page.getByTestId("source-panel")).toBeVisible();
  await page.getByRole("button", { name: "Collapse sources" }).click();
  await expect(page.getByTestId("source-panel")).toHaveCount(0);
  await expect(page.getByTestId("source-panel-open")).toBeVisible();
  await page.setViewportSize(PHONE);
  await expect(page.getByTestId("source-panel")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByTestId("source-panel-open")).toBeVisible();
  await expect(page.getByTestId("source-panel")).toHaveCount(0);

  // 2. Reopen the dock; a phone round trip leaves it open and the sheet closed.
  await page.getByTestId("source-panel-open").click();
  await expect(page.getByTestId("source-panel")).toBeVisible();
  await page.setViewportSize(PHONE);
  await expect(page.getByTestId("source-panel")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // 3. A sheet the user opened at phone width does not follow them across lg and back.
  await page.getByTestId("source-panel-open").click();
  await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByTestId("source-panel")).toBeVisible(); // the dock (open from step 2)
  await expect(page.locator("main").first()).not.toHaveAttribute("inert", "");
  await page.setViewportSize(PHONE);
  await expect(page.getByTestId("source-panel")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("main").first()).not.toHaveAttribute("inert", "");
  await expect(page.getByTestId("source-panel-open")).toBeVisible();
});

test("A: the nav follows the same rule: the lg rail keeps the user's choice, the phone drawer never opens by crossing", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
  const nav = page.getByTestId("left-nav");
  await expect(nav.getByRole("link", { name: "Chat" })).toBeVisible(); // operate starts expanded
  // The user collapses it to the rail; a phone round trip leaves it a rail.
  await page.getByRole("button", { name: "Toggle sidebar" }).click();
  await expect(nav.getByRole("link", { name: "Chat" })).toHaveCount(0);
  await page.setViewportSize(PHONE);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(nav).toBeHidden();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(nav).toBeVisible();
  await expect(nav.getByRole("link", { name: "Chat" })).toHaveCount(0);
  // A drawer opened at phone width is closed when the window grows, and stays closed on the way back.
  await page.setViewportSize(PHONE);
  await page.getByRole("button", { name: "More pages" }).click();
  await expect(page.getByRole("dialog", { name: "Primary navigation" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.setViewportSize(PHONE);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("main").first()).not.toHaveAttribute("inert", "");
});

test("A: a page loaded at phone width has its dock open when it grows to lg", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("source-panel")).toHaveCount(0);
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByTestId("source-panel")).toBeVisible();
});

// ---------------------------------------------------------------------------------------------
// B. nav markup
// ---------------------------------------------------------------------------------------------

test.describe("B: nav landmark and dialog markup", () => {
  test.describe("lg", () => {
    test.use({ viewport: { width: 1280, height: 800 } });

    for (const mode of ["cream", "graphite"] as const) {
      test(`${mode}: the docked nav is one <nav aria-label="Primary"> with no dialog role and no wrapper`, async ({ page }) => {
        await page.goto("/");
        await page.waitForLoadState("networkidle");
        if (mode === "graphite") await page.getByRole("button", { name: "Switch to operator mode" }).click();
        // Exact name: the phone dialog is "Primary navigation", which must not be this landmark.
        const nav = page.getByRole("navigation", { name: "Primary", exact: true });
        await expect(nav).toHaveCount(1);
        await expect(nav).toBeVisible();
        await expect(page.getByRole("navigation", { name: "Primary navigation", exact: true })).toHaveCount(0);
        const dom = await nav.evaluate((el) => ({
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute("role"),
          modal: el.getAttribute("aria-modal"),
          label: el.getAttribute("aria-label"),
          id: el.id,
          testid: el.getAttribute("data-testid"),
          parentTestid: el.parentElement?.getAttribute("data-testid"),
          inDialog: !!el.closest('[role="dialog"]'),
          nested: el.querySelectorAll("nav").length,
        }));
        expect(dom).toEqual({
          tag: "nav",
          role: null,
          modal: null,
          label: "Primary",
          id: "primary-nav",
          testid: "left-nav",
          parentTestid: "left-nav-slot",
          inDialog: false,
          nested: 0,
        });
        await expect(page.getByRole("dialog")).toHaveCount(0);
      });
    }
  });

  test.describe("phone", () => {
    test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

    test("the open drawer is a div dialog 'Primary navigation' that contains a navigation named Primary", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: "Switch to operator mode" }).click();
      await page.getByRole("button", { name: "More pages" }).click();

      const dialog = page.getByRole("dialog", { name: "Primary navigation", exact: true });
      await expect(dialog).toHaveCount(1);
      await expect(dialog).toBeVisible();
      const outer = await dialog.evaluate((el) => ({
        tag: el.tagName.toLowerCase(),
        modal: el.getAttribute("aria-modal"),
        id: el.id,
        testid: el.getAttribute("data-testid"),
      }));
      expect(outer).toEqual({ tag: "div", modal: "true", id: "primary-nav", testid: "left-nav" });

      // The landmark is inside the dialog, is a real <nav>, and carries no dialog role itself.
      const nav = dialog.getByRole("navigation", { name: "Primary", exact: true });
      await expect(nav).toHaveCount(1);
      const inner = await nav.evaluate((el) => ({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role"),
        modal: el.getAttribute("aria-modal"),
        parentIsDialog: el.parentElement?.getAttribute("role") === "dialog",
      }));
      expect(inner).toEqual({ tag: "nav", role: null, modal: null, parentIsDialog: true });
      // Nothing is named "Primary navigation" as a landmark, and no <nav> carries role=dialog.
      await expect(page.getByRole("navigation", { name: "Primary navigation", exact: true })).toHaveCount(0);
      expect(await page.locator('nav[role="dialog"]').count()).toBe(0);
      // The links live in the landmark, not beside it.
      const links = await dialog.getByRole("link").count();
      expect(links).toBeGreaterThan(3);
      expect(await nav.getByRole("link").count()).toBe(links);
      // The controls the other specs and the focus trap rely on are still where they were.
      await expect(page.getByTestId("left-nav").getByRole("link").first()).toBeFocused();
      // (The tab bar is inert behind the dialog, so it is not reachable by role here.)
      await expect(page.locator('button[aria-label="More pages"]')).toHaveAttribute("aria-controls", "primary-nav");
      await expect(page.locator("#primary-nav")).toHaveCount(1);

      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "More pages" })).toBeFocused();
    });
  });
});

// ---------------------------------------------------------------------------------------------
// C. mutation gates
// ---------------------------------------------------------------------------------------------

test.describe("C: Show me why while a drill is in flight", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

  /** Hold the drillthrough response until `release()`; count the requests that reached the network. */
  async function holdDrill(page: Page) {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const seen: string[] = [];
    await page.route("**/v1/chat/drillthrough", async (route) => {
      seen.push(route.request().postData() ?? "");
      await gate;
      await route.fulfill({ json: { rows: [{ sku: "SKU-A", qty: 3 }] } });
    });
    return { seen, release };
  }

  async function busyDrill(page: Page) {
    const held = await holdDrill(page);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await askQuestion(page);
    const why = page.getByTestId("answer-actions").last().getByRole("button", { name: "Show me why" });
    await why.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("answer-actions").last().getByRole("button", { name: /Drilling/ })).toBeVisible();
    return { ...held, button: page.getByTestId("answer-actions").last().getByRole("button", { name: /Drilling/ }) };
  }

  test("C1: a second activation while the drill is busy sends no second request", async ({ page }) => {
    const { seen, release, button } = await busyDrill(page);
    expect(seen).toHaveLength(1);
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    await button.dispatchEvent("click");
    // Give a wrongly-sent request time to reach the route handler.
    await page.waitForTimeout(400);
    expect(seen, "exactly one drillthrough request").toHaveLength(1);
    release();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    expect(seen).toHaveLength(1);
  });

  test("C2: while busy the button says Drilling, is aria-disabled, and is still the focused element", async ({ page }) => {
    const { release, button } = await busyDrill(page);
    await expect(button).toHaveText("Drilling…");
    await expect(button).toHaveAttribute("aria-disabled", "true");
    await expect(button).toBeFocused();
    release();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
  });

  test("C3: while busy the button is visibly dimmed (computed opacity under 1), and not after", async ({ page }) => {
    const { release, button } = await busyDrill(page);
    const opacity = () => button.evaluate((el) => Number(getComputedStyle(el).opacity));
    expect(await opacity()).toBeLessThan(1);
    release();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    await page.keyboard.press("Escape");
    const why = page.getByTestId("answer-actions").last().getByRole("button", { name: "Show me why" });
    await expect(why).toBeVisible();
    expect(await why.evaluate((el) => Number(getComputedStyle(el).opacity))).toBe(1);
  });
});

test.describe("C: Sources sheet behaviour by route and by opener", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

  test("C4: the sheet makes main inert on Chat only; a sheet left open under another route inerts nothing", async ({ page }) => {
    await page.goto("/library");
    await page.waitForLoadState("networkidle");
    // Client-side navigation to Chat so the router has a history entry to come back to.
    await page.getByTestId("tab-bar").getByRole("link", { name: "Chat" }).click();
    await expect(page.getByRole("textbox", { name: "Ask about your data" })).toBeVisible();
    await page.getByTestId("source-panel-open").click();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    // On Chat, where the sheet can open, main, the top bar and the tab bar are inert.
    await expect(page.locator("main").first()).toHaveAttribute("inert", "");
    await expect(page.locator("header")).toHaveAttribute("inert", "");
    await expect(page.getByTestId("tab-bar")).toHaveAttribute("inert", "");

    // Back to Library (browser history): the sheet's state is still "open", but there is no sheet on
    // this route, so nothing may be inert behind a dialog that is not there.
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("main").first()).not.toHaveAttribute("inert", "");
    await expect(page.locator("header")).not.toHaveAttribute("inert", "");
    await expect(page.getByTestId("tab-bar")).not.toHaveAttribute("inert", "");
    // The page is usable, not a dead surface.
    await page.getByTestId("tab-bar").getByRole("link", { name: "Chat" }).click();
    await expect(page.getByRole("textbox", { name: "Ask about your data" })).toBeVisible();
  });

  test("C5: the opener leaves the DOM while the sheet is open; Escape lands on the phone Sources button", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const opener = page.getByTestId("source-panel-open");
    // Keyboard-activate it so it holds focus when the sheet opens (a tap would not focus it).
    await opener.focus();
    await expect(opener).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    // The button that opened the sheet is no longer in the document: it is only rendered while the sheet is closed.
    await expect(page.getByTestId("source-panel-open")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // A fresh button was rendered; focus is on it, not lost on <body>.
    await expect(page.getByTestId("source-panel-open")).toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
  });

  test("C5b: a Show me why button removed while the sheet is open also falls back to the Sources button", async ({ page }) => {
    await page.route("**/v1/chat/drillthrough", (route) => route.fulfill({ json: { rows: [{ sku: "SKU-A", qty: 3 }] } }));
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await askQuestion(page);
    const why = page.getByTestId("answer-actions").last().getByRole("button", { name: "Show me why" });
    await why.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    // Detach the invoker (a re-render that drops it would do the same).
    await why.evaluate((el) => el.remove());
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("source-panel-open")).toBeFocused();
  });
});

test("C6: focus already inside the container when the modal opens is not taken as the invoker", async ({ page }) => {
  // Not reachable through the app: the sheet and the drawer mount when they open, so focus cannot
  // already be inside them. The hook is exercised directly in a harness mounted from the dev server.
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const landed = await page.evaluate(async () => {
    const mod = await import(/* @vite-ignore */ "/e2e/harness/modalFocusHarness.tsx");
    return mod.runInsideFocusCase();
  });
  expect(landed).toEqual({ whileOpen: "inside-first", afterClose: "opener" });
});
