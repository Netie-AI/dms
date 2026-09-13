import { expect, test } from "@playwright/test";

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

test.describe("STUDIO-MOBILE-01 desktop lg", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("chat keeps a docked Sources column", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("source-panel")).toBeVisible();
    await expect(page.getByTestId("source-panel-open")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /Ask (about your|your company's) data/ })).toBeVisible();
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
