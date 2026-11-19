import { expect, test } from "@playwright/test";

/**
 * IPHONE-INSTALL-01: the install metadata is correct for the shells that read it.
 *   1. manifest icons: "any" and "maskable" are separate entries, never the combined "any maskable"
 *      (a maskable icon served as "any" is cropped by launchers; Lighthouse and PWABuilder flag it).
 *      Both sizes (192, 512) exist for both purposes.
 *   2. the maskable icon keeps its glyph inside the safe zone (the central circle of 80% diameter),
 *      measured on the served PNG's pixels.
 *   3. <meta name="theme-color"> follows the mode, so the browser chrome around an installed or
 *      tabbed app matches the page in Operate mode (graphite), not only in cream.
 */

type Icon = { src: string; sizes: string; type: string; purpose?: string };

test("manifest lists separate any and maskable icons at 192 and 512", async ({ request }) => {
  const res = await request.get("/manifest.json");
  expect(res.ok()).toBe(true);
  const icons = ((await res.json()) as { icons: Icon[] }).icons;
  for (const i of icons) expect(i.purpose, JSON.stringify(i)).not.toMatch(/\s/);
  for (const purpose of ["any", "maskable"]) {
    for (const sizes of ["192x192", "512x512"]) {
      const hit = icons.filter((i) => i.purpose === purpose && i.sizes === sizes);
      expect(hit, `${purpose} ${sizes}`).toHaveLength(1);
      expect((await request.get(hit[0].src)).ok(), hit[0].src).toBe(true);
    }
  }
});

test("every maskable icon keeps its glyph inside the safe zone", async ({ page, request }) => {
  const icons = ((await (await request.get("/manifest.json")).json()) as { icons: Icon[] }).icons;
  const maskable = icons.filter((i) => i.purpose === "maskable");
  expect(maskable.length).toBeGreaterThanOrEqual(2);
  await page.goto("/");
  for (const icon of maskable) {
    const out = await page.evaluate(async (src) => {
      const img = new Image();
      img.src = src;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const g = c.getContext("2d")!;
      g.drawImage(img, 0, 0);
      const { data, width, height } = g.getImageData(0, 0, c.width, c.height);
      const bg = [data[0], data[1], data[2]];
      const limit = 0.4 * width;
      let outside = 0;
      let glyph = 0;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          const i = (y * width + x) * 4;
          const differs = Math.abs(data[i] - bg[0]) + Math.abs(data[i + 1] - bg[1]) + Math.abs(data[i + 2] - bg[2]) > 60;
          if (!differs) continue;
          glyph++;
          if (Math.hypot(x + 0.5 - width / 2, y + 0.5 - height / 2) > limit) outside++;
        }
      }
      // The corners must be the background colour all the way out: a launcher mask crops them.
      const corner = [0, (width - 1) * 4, (height - 1) * width * 4, ((height - 1) * width + width - 1) * 4].every(
        (o) => data[o] === bg[0] && data[o + 1] === bg[1] && data[o + 2] === bg[2],
      );
      return { outside, glyph, corner, width };
    }, icon.src);
    expect(out.glyph, `${icon.src} has a glyph`).toBeGreaterThan(200);
    expect(out.outside, `${icon.src} glyph pixels outside the safe circle`).toBe(0);
    expect(out.corner, `${icon.src} bleeds to the corners`).toBe(true);
  }
});

test("theme-color follows the mode", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  // A minified production build writes "#111" for "#111111": compare as rgb, not as strings.
  const rgb = (css: string | null) =>
    page.evaluate((c) => {
      const e = document.createElement("i");
      e.style.color = c ?? "";
      document.body.append(e);
      const v = getComputedStyle(e).color;
      e.remove();
      return v;
    }, css);
  const meta = async () => rgb(await page.evaluate(() => document.querySelector('meta[name="theme-color"]')?.getAttribute("content") ?? null));
  const panel = async () => rgb(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--color-panel").trim()));
  expect(await meta()).toBe(await panel());
  expect(await meta()).toBe("rgb(250, 248, 244)");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
  await expect(page.getByRole("button", { name: "Switch to operator mode" })).toHaveCount(0);
  expect(await panel()).toBe("rgb(17, 17, 17)");
  await expect.poll(meta).toBe("rgb(17, 17, 17)");
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect.poll(meta).toBe("rgb(17, 17, 17)");
});

test("the page-level overscroll lock is a phone rule: desktop keeps the browser's own gesture", async ({ page }) => {
  const overscroll = () => page.evaluate(() => getComputedStyle(document.body).overscrollBehaviorY);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(await overscroll(), "at lg a swipe-back or pull gesture is the browser's").toBe("auto");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await overscroll(), "below lg the shell scrolls its own panes").toBe("none");
});
