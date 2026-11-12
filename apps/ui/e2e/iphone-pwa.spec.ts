import { expect, test, type Locator } from "@playwright/test";

/**
 * IPHONE-PWA-01: the DMS UI installs to an iPhone home screen and is usable at
 * phone width. Every assertion is on what the phone user gets: the install
 * metadata Safari reads, and the rendered layout at 390x844.
 */

async function box(loc: Locator) {
  const b = await loc.boundingBox();
  expect(b).toBeTruthy();
  return b!;
}

test.describe("IPHONE-PWA-01 install metadata", () => {
  test("manifest is linked, standalone, and every icon it names is a real PNG", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    const href = await page.locator('link[rel="manifest"]').getAttribute("href");
    expect(href).toBeTruthy();
    const res = await request.get(href!);
    expect(res.ok()).toBe(true);
    const manifest = await res.json();
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/");
    expect(manifest.name).toMatch(/DMS/);
    const sizes = manifest.icons.map((i: { sizes: string }) => i.sizes);
    expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    for (const icon of manifest.icons as { src: string; sizes: string }[]) {
      const img = await request.get(icon.src);
      expect(img.ok(), icon.src).toBe(true);
      expect(img.headers()["content-type"]).toContain("image/png");
      // PNG IHDR: width at byte 16, big-endian. A mislabelled size is refused by iOS.
      const width = (await img.body()).readUInt32BE(16);
      expect(`${width}x${width}`).toBe(icon.sizes);
    }
  });

  test("iOS home-screen tags are present and the touch icon is 180px", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    await expect(page.locator('meta[name="apple-mobile-web-app-capable"]')).toHaveAttribute(
      "content",
      "yes",
    );
    await expect(page.locator('meta[name="viewport"]')).toHaveAttribute(
      "content",
      /viewport-fit=cover/,
    );
    const touch = await page.locator('link[rel="apple-touch-icon"]').getAttribute("href");
    expect(touch).toBeTruthy();
    const img = await request.get(touch!);
    expect(img.ok()).toBe(true);
    expect((await img.body()).readUInt32BE(16)).toBe(180);
  });
});

test.describe("IPHONE-PWA-01 phone layout 390x844", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("Sources sits in the chat composer, never on the chrome or a banner", async ({ page }) => {
    await page.goto("/");
    // The banners render after /health answers; measure once they are in.
    await page.waitForLoadState("networkidle");
    const open = page.getByTestId("source-panel-open");
    await expect(open).toBeVisible();
    const openBox = await box(open);
    const mainBox = await box(page.locator("main").first());
    // Everything above <main> is the top bar and the demo / offline banners
    // (CLAUDE.md rule 11: the demo banner must stay unmissable).
    expect(openBox.y).toBeGreaterThanOrEqual(mainBox.y);
    for (const banner of await page.getByRole("status").all()) {
      if (!(await banner.isVisible())) continue;
      const b = await box(banner);
      expect(openBox.y >= b.y + b.height || openBox.y + openBox.height <= b.y).toBe(true);
    }
    expect(openBox.height).toBeGreaterThanOrEqual(44);
  });

  test("the chat column uses the phone width", async ({ page }) => {
    await page.goto("/");
    const h1 = await box(page.getByRole("heading", { level: 1 }));
    expect(h1.x).toBeLessThanOrEqual(20);
    const chip = await box(page.getByRole("button", { name: /What|Top|Show|Divide/ }).first());
    expect(chip.x + chip.width).toBeGreaterThan(390 - 24);
  });

  test("an asked answer renders its text inside the screen", async ({ page }) => {
    await page.goto("/");
    const composer = page.getByPlaceholder(/Ask about your data/);
    await composer.fill("What was total revenue?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    const answer = page.getByTestId("answer-message").last();
    await expect(answer).toBeVisible({ timeout: 20_000 });
    await expect(answer).toContainText(/\S{3,}/);
    const a = await box(answer);
    expect(a.x).toBeGreaterThanOrEqual(0);
    expect(a.x + a.width).toBeLessThanOrEqual(390);
    expect(a.width).toBeGreaterThan(320);
  });

  test("top bar controls are 44px touch targets", async ({ page }) => {
    await page.goto("/");
    for (const loc of [
      page.locator("#space-switcher"),
      page.getByRole("button", { name: "New", exact: true }),
      page.getByRole("button", { name: "Switch to operator mode" }),
    ]) {
      expect((await box(loc)).height).toBeGreaterThanOrEqual(44);
    }
  });

  test("text fields are 16px so iOS Safari does not zoom on focus", async ({ page }) => {
    await page.goto("/");
    const size = (loc: Locator) => loc.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(await size(page.getByPlaceholder(/Ask about your data/))).toBeGreaterThanOrEqual(16);
    expect(await size(page.locator("#space-switcher"))).toBeGreaterThanOrEqual(16);
    await page.goto("/library");
    expect(await size(page.getByPlaceholder(/Filter files/))).toBeGreaterThanOrEqual(16);
  });

  test("Library stacks the tree above the detail instead of squeezing both", async ({ page }) => {
    await page.goto("/library");
    const tree = await box(page.getByTestId("library-tree"));
    const detail = await box(page.getByTestId("library-detail"));
    expect(tree.width).toBeGreaterThan(340);
    expect(detail.width).toBeGreaterThan(340);
    expect(detail.y).toBeGreaterThanOrEqual(tree.y + tree.height - 1);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(1);
  });
});
