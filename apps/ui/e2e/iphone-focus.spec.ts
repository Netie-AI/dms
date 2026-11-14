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
      // Focus can land outside by a tap on the top bar (not covered by the backdrop).
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
