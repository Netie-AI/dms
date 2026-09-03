import { expect, test, type Page } from "@playwright/test";

const ROUTES: { path: string; heading: string | RegExp }[] = [
  { path: "/", heading: /Ask (about your|your company's) data/ },
  { path: "/spaces", heading: "Spaces" },
  { path: "/library", heading: "Library" },
  { path: "/studio", heading: "Studio" },
  { path: "/ontology", heading: "Ontology" },
  { path: "/amend", heading: "Amend" },
  { path: "/audit", heading: "Audit" },
  { path: "/trust", heading: "Trust" },
  { path: "/runs", heading: "Runs" },
];

async function waitForChrome(page: Page) {
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  const chat = page.getByRole("link", { name: "Chat", exact: true });
  if (!(await chat.isVisible())) {
    await page.getByRole("button", { name: "Toggle sidebar" }).click();
  }
  await expect(chat).toBeVisible();
}

async function toOperate(page: Page) {
  const btn = page.getByRole("button", { name: "Switch to operator mode" });
  if (await btn.isVisible()) {
    await btn.click();
  }
  await expect(page.locator("html")).toHaveAttribute("data-theme", "graphite");
}

test.describe("DMS product chrome", () => {
  test("each surface renders its heading and primary nav", async ({ page }) => {
    for (const route of ROUTES) {
      await page.goto(route.path);
      await waitForChrome(page);
      await expect(page.getByRole("heading", { name: route.heading, level: 1 })).toBeVisible();
    }
  });

  test("admin is hidden for steward and visible after switching role", async ({ page }) => {
    await page.goto("/");
    await waitForChrome(page);
    await toOperate(page);
    await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
    await page.getByLabel("Role").selectOption("admin");
    await expect(page.getByRole("link", { name: "Admin" })).toBeVisible();
    await page.getByRole("link", { name: "Admin" }).click();
    await expect(page.getByRole("heading", { name: "Admin", level: 1 })).toBeVisible();
  });

  test("space switcher and New menu reach Studio and Spaces", async ({ page }) => {
    await page.goto("/");
    await waitForChrome(page);
    await toOperate(page);
    const switcher = page.getByLabel("Space");
    await expect(switcher).toBeVisible();
    const options = await switcher.locator("option").allTextContents();
    expect(options.some((t) => t.includes("Company (default ACL)"))).toBeTruthy();
    expect(options.some((t) => t.includes("Finance") || t.includes("Warehouse"))).toBeTruthy();

    await page.getByRole("button", { name: "+ New" }).click();
    await page.getByRole("button", { name: "Upload source" }).click();
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();

    await page.goto("/");
    await waitForChrome(page);
    await page.getByRole("button", { name: "+ New" }).click();
    await page.getByRole("button", { name: "New Space" }).click();
    await expect(page.getByRole("heading", { name: "Spaces", level: 1 })).toBeVisible();
  });

  test("theme toggle flips html data-theme", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "cream");
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "graphite");
    await page.getByRole("button", { name: "Switch to ask mode" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "cream");
  });
});
