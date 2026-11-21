import { expect, test, type Page } from "@playwright/test";

/**
 * Focus survives a crossing of lg (round 9 of the PR #510 checker).
 *
 * Below lg the open nav is `div#primary-nav[role=dialog] > nav[aria-label=Primary]`; at lg it is a
 * single `nav#primary-nav`. The Sources dock at lg is a different node from the phone composer's
 * Sources button. Crossing lg therefore destroys whichever of these held focus, and the browser
 * drops focus on <body>. These tests pin where focus lands instead: on the equivalent control, and
 * never on body. They also pin the other half: a crossing that destroys nothing the user was in
 * leaves focus exactly where it was.
 *
 * Every assertion reads `document.activeElement`, not a count of key presses.
 */

const PHONE = { width: 390, height: 844 };
const LG = { width: 1280, height: 800 };
const MID = { width: 800, height: 800 };
const ASK = /Ask about your data/;

type Active = { tag: string; testid: string | null; label: string | null; href: string | null; body: boolean; zone: string | null };

async function active(page: Page): Promise<Active> {
  return page.evaluate(() => {
    const a = document.activeElement as HTMLElement;
    return {
      tag: a.tagName.toLowerCase(),
      testid: a.getAttribute("data-testid"),
      label: a.getAttribute("aria-label"),
      href: a.getAttribute("href"),
      body: a === document.body,
      zone: a.closest("#primary-nav")?.tagName.toLowerCase() ?? null,
    };
  });
}

/**
 * Resize across lg and wait until the page has seen the media-query change and painted two frames
 * after it (any focus hand-off runs in the first of them), so a read of focus afterwards is not
 * racing the resize.
 */
async function cross(page: Page, size: { width: number; height: number }) {
  await page.evaluate(() => {
    (window as unknown as { __crossed: Promise<void> }).__crossed = new Promise<void>((resolve) =>
      window.matchMedia("(min-width: 1024px)").addEventListener("change", () => resolve(), { once: true }),
    );
  });
  await page.setViewportSize(size);
  await page.evaluate(async () => {
    await (window as unknown as { __crossed: Promise<void> }).__crossed;
    for (let i = 0; i < 3; i++) await new Promise<void>((r) => requestAnimationFrame(() => r()));
  });
}

async function operate(page: Page) {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
}

test.describe("nav: a focused link survives the drawer to docked-nav swap", () => {
  test("phone drawer open, focus on a link, widen to lg: the same link (by href) at lg", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await operate(page);
    await page.getByRole("button", { name: "More pages" }).click();
    const dialog = page.getByRole("dialog", { name: "Primary navigation" });
    await expect(dialog).toBeVisible();
    const library = dialog.locator('a[href="/library"]');
    await library.focus();
    await expect(library).toBeFocused();

    await cross(page, LG);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // Operating at phone width leaves the lg nav as the rail (it starts collapsed there); the link
    // is a different node but the same href.
    expect(await active(page)).toMatchObject({ tag: "a", href: "/library", body: false, zone: "nav" });
    await expect(page.locator('nav#primary-nav a[href="/library"]')).toBeFocused();
  });

  test("the same, with the lg nav expanded (loaded at lg, narrowed, drawer opened, widened)", async ({ page }) => {
    await page.setViewportSize(LG);
    await operate(page);
    await expect(page.getByTestId("left-nav").getByRole("link", { name: "Ontology" })).toBeVisible();
    await page.setViewportSize(PHONE);
    await page.getByRole("button", { name: "More pages" }).click();
    const dialog = page.getByRole("dialog", { name: "Primary navigation" });
    const ontology = dialog.locator('a[href="/ontology"]');
    await ontology.focus();
    await expect(ontology).toBeFocused();

    await cross(page, LG);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await active(page)).toMatchObject({ tag: "a", href: "/ontology", body: false, zone: "nav" });
    await expect(page.getByTestId("left-nav").getByRole("link", { name: "Ontology" })).toBeFocused();
  });

  test("widen with focus on the drawer's select (no link match): the first link, not body", async ({ page }) => {
    await page.setViewportSize(LG);
    await operate(page);
    await page.setViewportSize(PHONE);
    await page.getByRole("button", { name: "More pages" }).click();
    const dialog = page.getByRole("dialog", { name: "Primary navigation" });
    await dialog.getByRole("combobox", { name: "Role" }).focus();
    await expect(dialog.getByRole("combobox", { name: "Role" })).toBeFocused();

    await cross(page, LG);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const now = await active(page);
    expect(now.body, JSON.stringify(now)).toBe(false);
    expect(now).toMatchObject({ tag: "a", zone: "nav" });
    await expect(page.getByTestId("left-nav").getByRole("link").first()).toBeFocused();
  });

  test("lg expanded nav, focus on a link, narrow to phone: the More pages button, not body", async ({ page }) => {
    await page.setViewportSize(LG);
    await operate(page);
    const link = page.getByTestId("left-nav").getByRole("link", { name: "Amend" });
    await link.focus();
    await expect(link).toBeFocused();

    await cross(page, PHONE);
    const now = await active(page);
    expect(now.body, JSON.stringify(now)).toBe(false);
    await expect(page.getByRole("button", { name: "More pages" })).toBeFocused();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("lg expanded nav, focus on Library, narrow to phone: the Library tab (same href), not body", async ({ page }) => {
    await page.setViewportSize(LG);
    await operate(page);
    const link = page.getByTestId("left-nav").getByRole("link", { name: "Library" });
    await link.focus();
    await expect(link).toBeFocused();

    await cross(page, PHONE);
    expect(await active(page)).toMatchObject({ tag: "a", href: "/library", body: false });
    await expect(page.getByTestId("tab-bar").getByRole("link", { name: "Library" })).toBeFocused();
  });

  test("lg: Toggle sidebar focused, narrow to phone: the More pages button, not body", async ({ page }) => {
    await page.setViewportSize(LG);
    await operate(page);
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    await toggle.focus();
    await expect(toggle).toBeFocused();

    await cross(page, PHONE);
    const now = await active(page);
    expect(now.body, JSON.stringify(now)).toBe(false);
    await expect(page.getByRole("button", { name: "More pages" })).toBeFocused();
  });
});

test.describe("sources: a focused Sources control survives the dock to opener swap", () => {
  test("lg dock open, Collapse sources focused, narrow to 800: the composer Sources button", async ({ page }) => {
    await page.setViewportSize(LG);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const collapse = page.getByRole("button", { name: "Collapse sources" });
    await expect(collapse).toBeVisible();
    await collapse.focus();
    await expect(collapse).toBeFocused();

    await cross(page, MID);
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
    const now = await active(page);
    expect(now.body, JSON.stringify(now)).toBe(false);
    expect(now.testid).toBe("source-panel-open");
    await expect(page.getByTestId("source-panel-open")).toBeFocused();
    // The restore is focus only: nothing opened.
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("lg dock collapsed, the rail opener focused, narrow to phone: the composer Sources button", async ({ page }) => {
    await page.setViewportSize(LG);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Collapse sources" }).click();
    const rail = page.getByTestId("source-panel-open");
    await expect(rail).toHaveAccessibleName("Open sources");
    await rail.focus();
    await expect(rail).toBeFocused();

    await cross(page, PHONE);
    const now = await active(page);
    expect(now.body, JSON.stringify(now)).toBe(false);
    expect(now.testid).toBe("source-panel-open");
    await expect(page.getByTestId("source-panel-open")).toHaveAccessibleName(/^Sources: /);
    await expect(page.getByTestId("source-panel-open")).toBeFocused();
  });

  test("phone composer Sources button focused, widen to lg (dock open): the composer, not body", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const opener = page.getByTestId("source-panel-open");
    await opener.focus();
    await expect(opener).toBeFocused();

    await cross(page, LG);
    await expect(page.getByTestId("source-panel")).toBeVisible();
    const now = await active(page);
    expect(now.body, JSON.stringify(now)).toBe(false);
    await expect(page.getByPlaceholder(ASK)).toBeFocused();
  });

  test("phone sheet open, Collapse sources focused, widen to lg with the dock open: that same button keeps focus", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.getByTestId("source-panel-open").click();
    const collapse = page.getByRole("button", { name: "Collapse sources" });
    await expect(collapse).toBeFocused();
    const node = await collapse.elementHandle();

    await cross(page, LG);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // The aside is the same node at lg (the dock is open), so the focused control was not destroyed:
    // the hand-off must leave it alone rather than move focus to the composer.
    await expect(collapse).toBeFocused();
    expect(await collapse.evaluate((el, n) => el === n, node)).toBe(true);
  });
});

test("a hidden control that no hand-off owns is not given to the nav or the Sources button", async ({ page }) => {
  // "Manage Spaces" is `hidden lg:flex`: it disappears below lg and belongs to neither the nav nor
  // Sources. Each hand-off must return null for a control that is not its own.
  await page.setViewportSize(LG);
  await operate(page);
  const manage = page.getByRole("link", { name: "Manage Spaces" });
  await manage.focus();
  await expect(manage).toBeFocused();
  await cross(page, PHONE);
  const now = await active(page);
  expect(now.testid, JSON.stringify(now)).not.toBe("source-panel-open");
  expect(now.zone, JSON.stringify(now)).toBeNull();
  expect(now.label, JSON.stringify(now)).not.toBe("More pages");
});

test.describe("a crossing that destroys nothing the user was in leaves focus alone", () => {
  for (const [from, to] of [
    [LG, PHONE],
    [PHONE, LG],
    [LG, MID],
  ] as const) {
    test(`${from.width} to ${to.width}: the Space switcher keeps focus`, async ({ page }) => {
      await page.setViewportSize(from);
      await operate(page);
      const sw = page.getByLabel("Space", { exact: true });
      await sw.focus();
      await expect(sw).toBeFocused();
      await cross(page, to);
      await expect(sw).toBeFocused();
    });

    test(`${from.width} to ${to.width}: a focused composer keeps focus and its draft`, async ({ page }) => {
      await page.setViewportSize(from);
      await operate(page);
      const composer = page.getByPlaceholder(ASK);
      await composer.click();
      await composer.fill("draft kept");
      await expect(composer).toBeFocused();
      await cross(page, to);
      await expect(composer).toBeFocused();
      await expect(composer).toHaveValue("draft kept");
    });
  }
});
