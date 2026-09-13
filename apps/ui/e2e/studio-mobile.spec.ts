import { expect, test, type Page } from "@playwright/test";

async function mainWidth(page: Page): Promise<number> {
  const box = await page.locator("main").boundingBox();
  return box?.width ?? 0;
}

async function documentOverflowX(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test.describe("STUDIO-MOBILE-01 live 390px repro", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("open Sources must not zero main; no horizontal page scroll", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
    expect(await mainWidth(page)).toBeGreaterThan(200);
    expect(await documentOverflowX(page)).toBeLessThanOrEqual(1);

    await page.getByTestId("source-panel-open").click();
    await expect(page.getByTestId("source-panel")).toBeVisible();
    const slot = page.getByTestId("source-panel-slot");
    const slotBox = await slot.boundingBox();
    expect(slotBox?.width ?? 0).toBeLessThan(8);
    expect(await mainWidth(page)).toBeGreaterThan(200);
    await expect(page.getByRole("heading", { name: /Ask (about your|your company's) data/ })).toBeVisible();
    expect(await documentOverflowX(page)).toBeLessThanOrEqual(1);
    await expect(page.getByRole("button", { name: "New", exact: true })).toBeVisible();
    await expect(page.getByTestId("topbar-title")).toHaveText("netie");
    await expect(page.getByRole("link", { name: "Manage Spaces" })).toBeHidden();
    await expect(page.getByText("Operate", { exact: true })).toBeVisible();
    await expect(page.getByText("Switch to Operate")).toBeHidden();
    await expect(page.getByText("+ New")).toBeHidden();
  });

  test("graphite composer typed text uses ink, not a transparent UA color", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "graphite");
    const composer = page.getByPlaceholder(/Ask about your data/);
    await composer.fill("typed");
    const { color, caret } = await composer.evaluate((el) => {
      const s = getComputedStyle(el);
      return { color: s.color, caret: s.caretColor };
    });
    expect(color).toBe("rgb(229, 229, 229)");
    expect(caret).toBe("rgb(229, 229, 229)");
  });
});

test.describe("STUDIO-MOBILE-01 phone-width", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("chat ask stays on screen while Sources starts closed", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /Ask (about your|your company's) data/ }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeVisible();
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
    const open = page.getByTestId("source-panel-open");
    await expect(open).toBeVisible();
    const box = await open.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    await open.click();
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Collapse sources" }).click();
    await expect(page.getByTestId("source-panel")).toHaveCount(0);
  });

  test("studio files and SQLSRC start collapsed with an explicit control", async ({
    page,
  }) => {
    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();
    await expect(page.getByTestId("studio-files")).toBeHidden();
    await expect(page.getByTestId("sql-source-panel")).toBeHidden();
    const toggle = page.getByTestId("studio-sources-toggle");
    await expect(toggle).toBeVisible();
    const box = await toggle.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    await toggle.click();
    await expect(page.getByTestId("studio-files")).toBeVisible();
    await expect(page.getByTestId("sql-source-panel")).toBeVisible();
  });
});

test.describe("STUDIO-MOBILE-02 operate 390px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("Operate LeftNav is a closed drawer; chat and TopBar stay usable", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "graphite");

    await expect(page.getByTestId("left-nav")).toBeHidden();
    const slot = page.getByTestId("left-nav-slot");
    const slotBox = await slot.boundingBox();
    expect(slotBox?.width ?? 0).toBeLessThan(8);
    expect(await mainWidth(page)).toBeGreaterThan(200);
    await expect(page.getByRole("heading", { name: "Ask about your data" })).toBeVisible();
    expect(await documentOverflowX(page)).toBeLessThanOrEqual(1);

    const headerOverflow = await page.locator("header").evaluate(
      (el) => el.scrollWidth - el.clientWidth,
    );
    expect(headerOverflow).toBeLessThanOrEqual(1);
    await expect(page.getByTestId("topbar-title")).toBeVisible();
    await expect(page.getByRole("button", { name: "New", exact: true })).toBeVisible();
    await expect(page.getByLabel("Space", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Switch to ask mode" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Manage Spaces" })).toBeHidden();

    await page.getByRole("button", { name: "Toggle sidebar" }).click();
    await expect(page.getByTestId("left-nav")).toBeVisible();
    await expect(page.getByTestId("left-nav-backdrop")).toBeVisible();
    await expect(page.getByRole("link", { name: "Studio" })).toBeVisible();
    expect(await mainWidth(page)).toBeGreaterThan(200);

    await page.getByRole("link", { name: "Chat", exact: true }).click();
    await expect(page.getByTestId("left-nav")).toBeHidden();
    await expect(page.getByRole("heading", { name: "Ask about your data" })).toBeVisible();

    await expect(page.getByTestId("source-panel")).toHaveCount(0);
    await page.getByTestId("source-panel-open").click();
    await expect(page.getByTestId("source-panel")).toBeVisible();
    const sourceSlot = page.getByTestId("source-panel-slot");
    const sourceBox = await sourceSlot.boundingBox();
    expect(sourceBox?.width ?? 0).toBeLessThan(8);
    expect(await mainWidth(page)).toBeGreaterThan(200);
    expect(await documentOverflowX(page)).toBeLessThanOrEqual(1);
  });
});

test.describe("STUDIO-MOBILE-01 desktop lg", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("chat keeps a docked Sources column", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByTestId("source-panel-open")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /Ask (about your|your company's) data/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "+ New" })).toBeVisible();
    await expect(page.getByText("Switch to Operate")).toBeVisible();
    await expect(page.getByText("Manage", { exact: true })).toBeVisible();
    await expect(page.getByTestId("topbar-title")).toBeHidden();
  });

  test("studio keeps the two-column files grid without a mobile toggle", async ({
    page,
  }) => {
    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();
    await expect(page.getByTestId("studio-sources-toggle")).toBeHidden();
    await expect(page.getByTestId("studio-files")).toBeVisible();
    await expect(page.getByTestId("sql-source-panel")).toBeVisible();
  });
});
