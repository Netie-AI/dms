import { expect, test, type Page } from "@playwright/test";

/**
 * IPHONE-ROTATE-01: the phone composer grows with its text (one line until the text needs more,
 * capped at 148px). The height was measured only when the draft, the width class or the "asking"
 * flag changed, so rotating the phone (390 to 844 wide, both below lg) left the old height: a draft
 * that wrapped to four lines in portrait stayed four lines tall in landscape, and a draft that fit
 * on two landscape lines was clipped inside a two-line box in portrait. A person reads this as
 * text hidden in the box. Assertions are on the box and on what the box can show.
 */

const PORTRAIT = { width: 390, height: 844 };
const LANDSCAPE = { width: 844, height: 390 };
const SMALL = { width: 320, height: 568 };
// About 110 characters: four lines in the 238px field of a 390px phone (under the 148px cap), two at 844.
const DRAFT = "Which suppliers had a late delivery last quarter, and what was the total value of the late orders for each one?";

async function open(page: Page, mode: "cream" | "graphite") {
  await page.setViewportSize(PORTRAIT);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  if (mode === "graphite") {
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.getByRole("button", { name: "Switch to operator mode" })).toHaveCount(0);
  }
}

/** The composer's state, and the height a fresh measure of the same text would give. */
const read = (page: Page) =>
  page.getByPlaceholder(/Ask/).evaluate((el: HTMLTextAreaElement) => {
    const h = el.getBoundingClientRect().height;
    const style = el.style.height;
    el.style.height = "";
    const fresh = Math.round(Math.min(el.scrollHeight + 2, 148) * 100) / 100;
    el.style.height = style;
    return {
      h: Math.round(h * 100) / 100,
      fresh: Math.max(fresh, 44),
      clipped: el.scrollHeight > el.clientHeight + 1,
      value: el.value,
    };
  });

async function settle(page: Page) {
  await page.evaluate(async () => {
    for (let i = 0; i < 3; i++) await new Promise<void>((r) => requestAnimationFrame(() => r()));
  });
}

for (const mode of ["cream", "graphite"] as const) {
  test.describe(`composer rotation ${mode}`, () => {
    test(`${mode}: portrait to landscape shrinks the box to the text, nothing is clipped`, async ({ page }) => {
      await open(page, mode);
      const box = page.getByPlaceholder(/Ask/);
      await box.fill(DRAFT);
      const portrait = await read(page);
      expect(portrait.clipped).toBe(false);
      expect(portrait.h).toBeGreaterThan(80);
      await page.setViewportSize(LANDSCAPE);
      await settle(page);
      const land = await read(page);
      expect(land.value).toBe(DRAFT);
      expect(land.clipped).toBe(false);
      expect(land.h).toBeLessThan(portrait.h);
      expect(land.h).toBeCloseTo(land.fresh, 0);
    });

    test(`${mode}: landscape to portrait grows the box, the whole draft is visible`, async ({ page }) => {
      await open(page, mode);
      await page.setViewportSize(LANDSCAPE);
      await settle(page);
      const box = page.getByPlaceholder(/Ask/);
      await box.fill(DRAFT);
      const land = await read(page);
      expect(land.clipped).toBe(false);
      await page.setViewportSize(PORTRAIT);
      await settle(page);
      const portrait = await read(page);
      expect(portrait.value).toBe(DRAFT);
      expect(portrait.clipped, "text scrolls inside a box that is too short").toBe(false);
      expect(portrait.h).toBeGreaterThan(land.h);
      expect(portrait.h).toBeCloseTo(portrait.fresh, 0);
    });
  });
}

test("320 wide: the cap holds (148px) and long text scrolls inside it, then rotates back to one line of room", async ({ page }) => {
  await open(page, "cream");
  await page.setViewportSize(SMALL);
  const box = page.getByPlaceholder(/Ask/);
  await box.fill(DRAFT.repeat(2));
  const small = await read(page);
  expect(small.h).toBeLessThanOrEqual(148);
  await page.setViewportSize(LANDSCAPE);
  await settle(page);
  const land = await read(page);
  expect(land.value).toBe(DRAFT.repeat(2));
  expect(land.h).toBeCloseTo(land.fresh, 0);
  expect(land.h).toBeLessThan(small.h);
});

test("an empty composer stays one line through a rotation", async ({ page }) => {
  await open(page, "cream");
  const one = (await read(page)).h;
  await page.setViewportSize(LANDSCAPE);
  await settle(page);
  expect((await read(page)).h).toBe(one);
  await page.setViewportSize(PORTRAIT);
  await settle(page);
  expect((await read(page)).h).toBe(one);
});

test("rotations raise no window error (a ResizeObserver loop is noise in every error tracker)", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __errs: string[] }).__errs = [];
    window.addEventListener("error", (e) => (window as unknown as { __errs: string[] }).__errs.push(e.message));
  });
  await open(page, "cream");
  await page.getByPlaceholder(/Ask/).fill(DRAFT);
  for (const size of [LANDSCAPE, PORTRAIT, LANDSCAPE, PORTRAIT]) {
    await page.setViewportSize(size);
    await settle(page);
  }
  expect(await page.evaluate(() => (window as unknown as { __errs: string[] }).__errs)).toEqual([]);
});

test("the cap is reached: a very long draft at 320 wide is exactly 148px tall and scrolls inside", async ({ page }) => {
  await open(page, "cream");
  await page.setViewportSize(SMALL);
  await page.getByPlaceholder(/Ask/).fill(DRAFT.repeat(4));
  const s = await read(page);
  expect(s.h).toBe(148);
  expect(s.clipped, "past the cap the text scrolls inside the box").toBe(true);
});

test("at lg the composer is the person's: a dragged height survives a resize, and widening from a phone leaves no inline height", async ({ page }) => {
  await open(page, "cream");
  await page.setViewportSize({ width: 1280, height: 800 });
  const box = page.getByPlaceholder(/Ask/);
  await box.fill(DRAFT);
  await box.evaluate((el: HTMLTextAreaElement) => (el.style.height = "200px"));
  await page.setViewportSize({ width: 1100, height: 800 });
  await settle(page);
  expect(await box.evaluate((el: HTMLTextAreaElement) => el.style.height), "resize-y drag lost").toBe("200px");
  await page.setViewportSize(PORTRAIT);
  await settle(page);
  expect(await box.evaluate((el: HTMLTextAreaElement) => el.style.height)).not.toBe("");
  await page.setViewportSize({ width: 1280, height: 800 });
  await settle(page);
  expect(await box.evaluate((el: HTMLTextAreaElement) => el.style.height), "phone height left at lg").toBe("");
});

test("a rotation does not move focus or lose the caret", async ({ page }) => {
  await open(page, "cream");
  const box = page.getByPlaceholder(/Ask/);
  await box.fill(DRAFT);
  await box.focus();
  await box.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(10, 25));
  await page.setViewportSize(LANDSCAPE);
  await settle(page);
  await expect(box).toBeFocused();
  expect(await box.evaluate((el: HTMLTextAreaElement) => [el.selectionStart, el.selectionEnd])).toEqual([10, 25]);
});
