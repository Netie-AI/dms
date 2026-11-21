import { expect, test, type Page } from "@playwright/test";

/**
 * Focus stays inside a modal phone overlay (WCAG 2.4.3, the ARIA dialog pattern).
 * The Sources sheet and the nav drawer are `role="dialog" aria-modal` below lg:
 * Tab and Shift+Tab wrap inside them, Escape and the backdrop give focus back to
 * the opener. At lg the Sources column is docked, there is no modal, and Tab
 * moves through the page as it always did.
 *
 * Assertions read `document.activeElement` (tag, accessible name, containment),
 * never only a count of key presses.
 */

const PHONE = { width: 390, height: 844 };

type Where = { tag: string; name: string; inside: boolean };

/** Where focus is now, and whether it is inside the element with this test id. */
async function where(page: Page, dialogId: string): Promise<Where> {
  return page.evaluate((id) => {
    const a = document.activeElement as HTMLElement;
    const dlg = document.querySelector(`[data-testid="${id}"]`);
    const name =
      a.getAttribute("aria-label") ||
      (a.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40) ||
      a.id ||
      "";
    return { tag: a.tagName.toLowerCase(), name, inside: !!dlg && dlg.contains(a) };
  }, dialogId);
}

/** The dialog's tab stops by visible name, in DOM order (what a keyboard user can reach). */
async function stops(page: Page, dialogId: string): Promise<string[]> {
  return page.evaluate((id) => {
    const dlg = document.querySelector(`[data-testid="${id}"]`)!;
    const sel = 'a[href], button, select, input, textarea, summary, [tabindex]:not([tabindex="-1"])';
    return [...dlg.querySelectorAll<HTMLElement>(sel)]
      .filter((el) => !(el as HTMLButtonElement).disabled && el.getClientRects().length > 0)
      .map(
        (el) =>
          el.getAttribute("aria-label") || (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40),
      );
  }, dialogId);
}

async function focusStop(page: Page, dialogId: string, index: number) {
  await page.evaluate(
    ({ id, index }) => {
      const dlg = document.querySelector(`[data-testid="${id}"]`)!;
      const sel = 'a[href], button, select, input, textarea, summary, [tabindex]:not([tabindex="-1"])';
      const all = [...dlg.querySelectorAll<HTMLElement>(sel)].filter(
        (el) => !(el as HTMLButtonElement).disabled && el.getClientRects().length > 0,
      );
      all.at(index)!.focus();
    },
    { id: dialogId, index },
  );
}

async function askQuestion(page: Page) {
  await page.getByPlaceholder(/Ask about your data/).fill("What was total revenue?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("answer-message").last()).toBeVisible({ timeout: 20_000 });
}

async function openSheet(page: Page, mode: "cream" | "graphite") {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  if (mode === "graphite") await page.getByRole("button", { name: "Switch to operator mode" }).click();
  await askQuestion(page);
  await page.getByTestId("source-panel-open").click();
  await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Collapse sources" })).toBeFocused();
}

async function openDrawer(page: Page) {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
  await page.getByRole("button", { name: "More pages" }).click();
  await expect(page.getByTestId("left-nav")).toBeVisible();
  await expect(page.getByTestId("left-nav").getByRole("link").first()).toBeFocused();
}

async function pressAndRecord(page: Page, dialogId: string, key: string, times: number) {
  const seen: Where[] = [];
  for (let i = 0; i < times; i++) {
    await page.keyboard.press(key);
    seen.push(await where(page, dialogId));
  }
  return seen;
}

test.describe("IPHONE-A11Y-01 focus is trapped in a modal phone overlay", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

  for (const mode of ["cream", "graphite"] as const) {
    test(`${mode}: Sources sheet, Tab never leaves the dialog and wraps`, async ({ page }) => {
      await openSheet(page, mode);
      const names = await stops(page, "source-panel");
      expect(names.length).toBeGreaterThanOrEqual(2);
      const seen = await pressAndRecord(page, "source-panel", "Tab", names.length * 2 + 3);
      for (const w of seen) expect(w, JSON.stringify(seen)).toMatchObject({ inside: true });
      // Tab visits every stop in DOM order and wraps from the last to the first.
      expect(seen.slice(0, names.length - 1).map((w) => w.name)).toEqual(names.slice(1));
      expect(seen[names.length - 1].name).toBe(names[0]);
    });

    test(`${mode}: Sources sheet, Shift+Tab never leaves the dialog and wraps`, async ({ page }) => {
      await openSheet(page, mode);
      const names = await stops(page, "source-panel");
      const seen = await pressAndRecord(page, "source-panel", "Shift+Tab", names.length * 2 + 3);
      for (const w of seen) expect(w, JSON.stringify(seen)).toMatchObject({ inside: true });
      // The first control's Shift+Tab goes to the last.
      expect(seen[0].name).toBe(names.at(-1));
      expect(seen[1].name).toBe(names.at(-2));
    });

    test(`${mode}: Sources sheet, last Tab goes to first and first Shift+Tab to last`, async ({ page }) => {
      await openSheet(page, mode);
      const names = await stops(page, "source-panel");
      await focusStop(page, "source-panel", -1);
      await page.keyboard.press("Tab");
      expect(await where(page, "source-panel")).toMatchObject({ name: names[0], inside: true });
      await page.keyboard.press("Shift+Tab");
      expect(await where(page, "source-panel")).toMatchObject({ name: names.at(-1), inside: true });
    });

    test(`${mode}: Sources sheet, Tab from a stray focus outside comes back in`, async ({ page }) => {
      await openSheet(page, mode);
      const names = await stops(page, "source-panel");
      // Focus can land outside by a tap on the top bar. The bar is inert while the sheet is open
      // (see the inert test below), so lift that here to model a browser that ignores `inert`:
      // the Tab guard must still bring focus back in.
      await page.evaluate(() => document.querySelector("header")!.removeAttribute("inert"));
      await page.evaluate(() => (document.getElementById("space-switcher") as HTMLElement).focus());
      expect((await where(page, "source-panel")).inside).toBe(false);
      await page.keyboard.press("Tab");
      expect(await where(page, "source-panel")).toMatchObject({ name: names[0], inside: true });
      await page.evaluate(() => (document.getElementById("space-switcher") as HTMLElement).focus());
      await page.keyboard.press("Shift+Tab");
      expect(await where(page, "source-panel")).toMatchObject({ name: names.at(-1), inside: true });
    });

    test(`${mode}: Sources sheet, Escape and the backdrop return focus to the opener`, async ({ page }) => {
      await openSheet(page, mode);
      await page.keyboard.press("Tab");
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: "Sources" })).toHaveCount(0);
      await expect(page.getByTestId("source-panel-open")).toBeFocused();

      await page.getByTestId("source-panel-open").click();
      await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
      await page.keyboard.press("Tab");
      await page.getByTestId("source-panel-backdrop").click({ position: { x: 195, y: 40 } });
      await expect(page.getByRole("dialog", { name: "Sources" })).toHaveCount(0);
      await expect(page.getByTestId("source-panel-open")).toBeFocused();
    });
  }

  {
    const mode = "graphite" as const;
    test(`${mode}: nav drawer, first and last stop are what the drawer shows first and last`, async ({ page }) => {
      await openDrawer(page);
      const names = await stops(page, "left-nav");
      // Read from the DOM: the first and last visible link or control in the drawer.
      const shown = await page.evaluate(() => {
        const nav = document.querySelector('[data-testid="left-nav"]')!;
        const all = [...nav.querySelectorAll<HTMLElement>("a[href], select")].filter(
          (el) => el.getClientRects().length > 0,
        );
        const label = (el: HTMLElement) =>
          el.getAttribute("aria-label") || (el.textContent || "").replace(/\s+/g, " ").trim();
        return { first: label(all[0]), last: label(all.at(-1)!), lastTag: all.at(-1)!.tagName.toLowerCase() };
      });
      expect(names[0]).toBe(shown.first);
      expect(names.at(-1)).toBe(shown.last);
      expect(shown.lastTag).toBe("select"); // the Role select ends the operator drawer
      await page.keyboard.press("Shift+Tab");
      expect(await where(page, "left-nav")).toMatchObject({ name: shown.last, inside: true });
      await page.keyboard.press("Tab");
      expect(await where(page, "left-nav")).toMatchObject({ name: shown.first, inside: true });
    });

    test(`${mode}: nav drawer, Tab never leaves the dialog and wraps`, async ({ page }) => {
      await openDrawer(page);
      const names = await stops(page, "left-nav");
      expect(names.length).toBeGreaterThanOrEqual(2);
      const seen = await pressAndRecord(page, "left-nav", "Tab", names.length * 2 + 3);
      for (const w of seen) expect(w, JSON.stringify(seen)).toMatchObject({ inside: true });
      expect(seen.slice(0, names.length - 1).map((w) => w.name)).toEqual(names.slice(1));
      expect(seen[names.length - 1].name).toBe(names[0]);
    });

    test(`${mode}: nav drawer, Shift+Tab never leaves the dialog and wraps`, async ({ page }) => {
      await openDrawer(page);
      const names = await stops(page, "left-nav");
      const seen = await pressAndRecord(page, "left-nav", "Shift+Tab", names.length * 2 + 3);
      for (const w of seen) expect(w, JSON.stringify(seen)).toMatchObject({ inside: true });
      expect(seen[0].name).toBe(names.at(-1));
      expect(seen[1].name).toBe(names.at(-2));
    });

    test(`${mode}: nav drawer, last Tab goes to first and first Shift+Tab to last`, async ({ page }) => {
      await openDrawer(page);
      const names = await stops(page, "left-nav");
      await focusStop(page, "left-nav", -1);
      await page.keyboard.press("Tab");
      expect(await where(page, "left-nav")).toMatchObject({ name: names[0], inside: true });
      await page.keyboard.press("Shift+Tab");
      expect(await where(page, "left-nav")).toMatchObject({ name: names.at(-1), inside: true });
    });

    test(`${mode}: nav drawer, Escape and the backdrop close it and return focus to the opener`, async ({ page }) => {
      await openDrawer(page);
      const more = page.getByRole("button", { name: "More pages" });
      await page.keyboard.press("Tab");
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("left-nav")).toBeHidden();
      await expect(more).toBeFocused();
      await more.click();
      await expect(page.getByTestId("left-nav")).toBeVisible();
      await page.keyboard.press("Tab");
      // The drawer is 208px wide; tap the backdrop to its right.
      await page.getByTestId("left-nav-backdrop").click({ position: { x: 350, y: 300 } });
      await expect(page.getByTestId("left-nav")).toBeHidden();
      await expect(more).toBeFocused();
    });
  }

  test("graphite: the nav drawer is a modal dialog with a name, and the top bar, main and tab bar are inert", async ({ page }) => {
    await openDrawer(page);
    const drawer = page.getByRole("dialog", { name: "Primary navigation" });
    await expect(drawer).toBeVisible();
    await expect(drawer).toHaveAttribute("aria-modal", "true");
    await expect(drawer).toHaveAttribute("data-testid", "left-nav");
    for (const behind of [page.locator("header"), page.locator("main"), page.getByTestId("tab-bar")]) {
      await expect(behind).toHaveAttribute("inert", "");
    }
    // Inert is real: focus cannot be placed on the top bar's controls.
    const stuck = await page.evaluate(() => {
      (document.getElementById("space-switcher") as HTMLElement).focus();
      return document.activeElement?.id === "space-switcher";
    });
    expect(stuck).toBe(false);
    // Closing lifts all of it.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    for (const behind of [page.locator("header"), page.locator("main"), page.getByTestId("tab-bar")]) {
      await expect(behind).not.toHaveAttribute("inert", "");
    }
  });

  test("Sources sheet: the top bar is inert while it is open and free again after", async ({ page }) => {
    await openSheet(page, "cream");
    await expect(page.locator("header")).toHaveAttribute("inert", "");
    await expect(page.locator("main")).toHaveAttribute("inert", "");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("header")).not.toHaveAttribute("inert", "");
    await expect(page.locator("main")).not.toHaveAttribute("inert", "");
  });

  test("Sources sheet: Escape returns focus to the control that opened it, not the Sources button", async ({ page }) => {
    await page.route("**/v1/chat/drillthrough", (route) =>
      route.fulfill({ json: { rows: [{ sku: "SKU-A", qty: 3 }] } }),
    );
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await askQuestion(page);
    const why = page.getByTestId("answer-actions").last().getByRole("button", { name: "Show me why" });
    await why.click();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Collapse sources" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Sources" })).toHaveCount(0);
    await expect(why).toBeFocused();
    // The backdrop gives it back the same way.
    await why.click();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    await page.getByTestId("source-panel-backdrop").click({ position: { x: 195, y: 40 } });
    await expect(page.getByRole("dialog", { name: "Sources" })).toHaveCount(0);
    await expect(why).toBeFocused();
  });

  // The guard lets the browser own Alt+Tab, Ctrl+Tab and Cmd+Tab (switch window or tab).
  for (const mod of ["Alt", "Control", "Meta"] as const) {
    test(`Sources sheet: ${mod}+Tab is not trapped or wrapped by the dialog`, async ({ page }) => {
      await openSheet(page, "cream");
      const names = await stops(page, "source-panel");
      expect(names.length).toBeGreaterThanOrEqual(2);
      await focusStop(page, "source-panel", -1);
      await page.keyboard.press(`${mod}+Tab`);
      // On the last stop, a plain Tab would wrap to the first; with a modifier it must not.
      const w = await where(page, "source-panel");
      expect(w.name, `${mod}+Tab wrapped focus to the first stop`).not.toBe(names[0]);
      expect(w).toMatchObject({ name: names.at(-1), inside: true });
      // Same from the first stop with Shift: no wrap to the last.
      await focusStop(page, "source-panel", 0);
      await page.keyboard.press(`${mod}+Shift+Tab`);
      expect((await where(page, "source-panel")).name).not.toBe(names.at(-1));
    });
  }

  test("the answer tools strip is a named group", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await askQuestion(page);
    const strip = page.getByRole("group", { name: "Answer tools" });
    await expect(strip).toHaveCount(1);
    await expect(strip).toHaveAttribute("data-testid", "answer-actions");
    await expect(strip.getByRole("button", { name: "Share answer" })).toBeVisible();
  });

  test("cream: there is no phone drawer to trap (no More pages, and crossing lg closes the nav)", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // All four Ask-mode pages are tabs, so the tab bar has no More button.
    await expect(page.getByTestId("tab-bar").getByRole("link")).toHaveCount(4);
    await expect(page.getByRole("button", { name: "More pages" })).toHaveCount(0);
    await expect(page.getByTestId("left-nav")).toBeHidden();
    // The only other way to open the nav is the lg toggle, and narrowing the window resets it closed.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.getByRole("button", { name: "Toggle sidebar" }).click();
    await expect(page.getByTestId("left-nav").getByRole("link", { name: "Chat" })).toBeVisible();
    await page.setViewportSize(PHONE);
    await expect(page.getByTestId("left-nav")).toBeHidden();
  });

  // A stop that is not a real tab stop must not count as the first or last one.
  for (const how of ["disabled", "inert", "visibility", "display"] as const) {
    test(`Sources sheet: a ${how} last control is not a stop, Tab wraps to the real last`, async ({ page }) => {
      await openSheet(page, "cream");
      const names = await stops(page, "source-panel");
      await focusStop(page, "source-panel", -2);
      await page.evaluate((how) => {
        const dlg = document.querySelector('[data-testid="source-panel"]')!;
        const btns = [...dlg.querySelectorAll<HTMLElement>("button")];
        const last = btns.at(-1)!;
        if (how === "disabled") (last as HTMLButtonElement).disabled = true;
        if (how === "inert") last.setAttribute("inert", "");
        if (how === "visibility") last.style.visibility = "hidden";
        if (how === "display") last.style.display = "none";
      }, how);
      const real = names.at(-2)!;
      expect(await where(page, "source-panel")).toMatchObject({ name: real, inside: true });
      await page.keyboard.press("Tab");
      expect(await where(page, "source-panel")).toMatchObject({ name: names[0], inside: true });
      await page.keyboard.press("Shift+Tab");
      expect(await where(page, "source-panel")).toMatchObject({ name: real, inside: true });
    });
  }

  test("Sources sheet: tabindex=-1 controls at the end are not stops", async ({ page }) => {
    await openSheet(page, "cream");
    const names = await stops(page, "source-panel");
    const real = names.at(-1)!;
    await page.evaluate(() => {
      const dlg = document.querySelector('[data-testid="source-panel"]')!;
      const b = document.createElement("button");
      b.tabIndex = -1;
      b.textContent = "skip-button";
      const a = document.createElement("a");
      a.href = "#skip";
      a.tabIndex = -1;
      a.textContent = "skip-link";
      dlg.append(b, a);
    });
    await focusStop(page, "source-panel", names.length - 1);
    expect(await where(page, "source-panel")).toMatchObject({ name: real, inside: true });
    await page.keyboard.press("Tab");
    expect(await where(page, "source-panel")).toMatchObject({ name: names[0], inside: true });
    await page.keyboard.press("Shift+Tab");
    const back = await where(page, "source-panel");
    expect(back).toMatchObject({ name: real, inside: true });
    expect(back.name).not.toMatch(/^skip-/);
  });

  test("Sources sheet: only the checked radio of a group is a stop", async ({ page }) => {
    await openSheet(page, "cream");
    const names = await stops(page, "source-panel");
    await page.evaluate(() => {
      const dlg = document.querySelector('[data-testid="source-panel"]')!;
      const box = document.createElement("div");
      box.innerHTML =
        '<input type="radio" name="g" aria-label="picked" checked><input type="radio" name="g" aria-label="other">';
      dlg.appendChild(box);
    });
    // DOM order ends: ..., picked (checked, a tab stop), other (unchecked, not a stop).
    await page.keyboard.press("Shift+Tab");
    expect(await where(page, "source-panel")).toMatchObject({ name: "picked", inside: true });
    await page.keyboard.press("Tab");
    expect(await where(page, "source-panel")).toMatchObject({ name: names[0], inside: true });
  });
});

test.describe("IPHONE-A11Y-01 no trap at lg", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("docked Sources is not a dialog and Tab walks the page, top bar included", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("source-panel")).not.toHaveAttribute("aria-modal", "true");
    await expect(page.getByTestId("tab-bar")).toHaveCount(0);

    // Tab from the Space select reaches the rest of the top bar, then the page.
    await page.evaluate(() => (document.getElementById("space-switcher") as HTMLElement).focus());
    const seen: Where[] = [];
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("Tab");
      seen.push(await where(page, "source-panel"));
    }
    expect(seen.map((w) => w.name)).toContain("Manage Spaces");
    expect(seen.map((w) => w.name)).toContain("Switch to operator mode");

    // From the last Sources control, Tab does not wrap to the first one.
    const names = await stops(page, "source-panel");
    expect(names.length).toBeGreaterThanOrEqual(1);
    await focusStop(page, "source-panel", -1);
    await page.keyboard.press("Tab");
    const after = await where(page, "source-panel");
    expect(after.inside && after.name === names[0] && names.length > 1).toBe(false);
    // Shift+Tab from the first Sources control goes back into the page, not to the last stop.
    await focusStop(page, "source-panel", 0);
    await page.keyboard.press("Shift+Tab");
    const back = await where(page, "source-panel");
    expect(back.inside && names.length > 1 && back.name === names.at(-1)).toBe(false);
  });

  test("docked Sources shrinking past lg starts closed: no uninvited sheet, focus and draft stay in the composer", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByTestId("source-panel")).toBeVisible();
    const composer = page.getByPlaceholder(/Ask about your data/);
    await composer.click();
    await composer.fill("revenue by region next quarter");
    await expect(composer).toBeFocused();

    await page.setViewportSize({ width: 800, height: 800 });
    // Not a sheet, not a dialog, main is live, focus and draft are where they were.
    const phoneComposer = page.getByRole("textbox", { name: "Ask about your data" });
    await expect(phoneComposer).toHaveValue("revenue by region next quarter");
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("main")).not.toHaveAttribute("inert", "");
    await expect(page.locator("header")).not.toHaveAttribute("inert", "");
    await expect(phoneComposer).toBeFocused();
    // Give any late effect or frame a chance to steal focus, then look again.
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await expect(phoneComposer).toBeFocused();
    await expect(page.getByTestId("source-panel")).toHaveCount(0);

    // The state is kept: the Sources button opens the sheet on demand, and Escape gives focus back to it.
    await page.getByTestId("source-panel-open").click();
    await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(phoneComposer).toHaveValue("revenue by region next quarter");

    // Growing back to lg docks it open again, as on a fresh desktop load.
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByPlaceholder(/Ask about your data/)).toHaveValue("revenue by region next quarter");
  });

  test("at lg nothing is inert and the nav and top bar carry no phone dialog markup", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.locator("header")).not.toHaveAttribute("inert", "");
    await expect(page.locator("main")).not.toHaveAttribute("inert", "");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await expect(nav).toBeVisible();
    await expect(nav).not.toHaveAttribute("role", /.*/);
    await expect(nav).not.toHaveAttribute("aria-modal", /.*/);
  });

  test("the nav is docked, not a drawer, and Tab is free of it", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    const nav = page.getByTestId("left-nav");
    await expect(nav).toBeVisible();
    await expect(nav).not.toHaveAttribute("aria-modal", "true");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("link", { name: "Runs" }).focus();
    await page.keyboard.press("Tab");
    // Role select at lg is hidden; Tab leaves the nav for the page (no wrap to Chat).
    const w = await where(page, "left-nav");
    expect(w.name).not.toBe("Chat");
    await page.getByRole("link", { name: "Runs" }).focus();
    await page.keyboard.press("Shift+Tab");
    expect(await where(page, "left-nav")).toMatchObject({ name: "Trust" });
  });
});
