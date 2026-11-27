import { expect, test, type Page } from "@playwright/test";

/**
 * Round 10 checker findings, pinned. The matrix (iphone-focus-matrix) accepts any equivalent
 * control, so these fix the cases it cannot see: a browser without Element.checkVisibility
 * (Safari before 17.4), tier order, a disabled or visibility:hidden twin, a decoy that comes AFTER
 * the real control in document order, and focus that the app itself moved before the hand-off.
 * Synthetic nodes are added to <main> by the test and swapped inside a later `change` listener,
 * which runs after the module's own, exactly like a component's.
 */
const PHONE = { width: 390, height: 844 };
const LG = { width: 1280, height: 800 };

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

const active = (page: Page) =>
  page.evaluate(() => {
    const a = document.activeElement as HTMLElement;
    return { body: a === document.body, tag: a.tagName.toLowerCase(), id: a.id };
  });

async function boot(page: Page) {
  await page.setViewportSize(LG);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
}

/** A section in <main> holding `before`, focused on #b1; at the crossing its content becomes `after`. */
async function swapAtCrossing(page: Page, before: string, after: string, extra = "") {
  await page.evaluate(
    ([b, a, x]) => {
      const s = document.createElement("section");
      s.setAttribute("aria-label", "synthetic");
      s.innerHTML = b;
      document.querySelector("main")!.append(s);
      document.getElementById("b1")!.focus();
      window.matchMedia("(min-width: 1024px)").addEventListener("change", () => {
        s.innerHTML = a;
        if (x) {
          const n = document.createElement("nav");
          n.setAttribute("aria-label", "Decoy");
          n.innerHTML = x;
          document.body.append(n);
        }
      });
    },
    [before, after, extra],
  );
}

test("no Element.checkVisibility (Safari before 17.4): the hand-off still lands, both ways, twice", async ({ page }) => {
  await page.addInitScript(() => {
    // @ts-expect-error simulate an old WebKit
    delete Element.prototype.checkVisibility;
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await boot(page);
  expect(await page.evaluate(() => typeof Element.prototype.checkVisibility)).toBe("undefined");
  for (let i = 0; i < 2; i++) {
    await page.getByTestId("left-nav").getByRole("link", { name: "Library" }).focus();
    await cross(page, PHONE);
    await expect(page.getByTestId("tab-bar").getByRole("link", { name: "Library" })).toBeFocused();
    await cross(page, LG);
    await expect(page.getByTestId("left-nav").getByRole("link", { name: "Library" })).toBeFocused();
  }
  expect(errors).toEqual([]);
});

test("a testid match beats a same-named control (tier order)", async ({ page }) => {
  await boot(page);
  await swapAtCrossing(
    page,
    '<button id="b1" data-testid="syn-id" type="button">Old name</button>',
    '<button id="byname" type="button">Old name</button><button id="byid" data-testid="syn-id" type="button">Renamed at phone</button>',
  );
  await cross(page, PHONE);
  expect((await active(page)).id).toBe("byid");
});

test("a disabled control first in the surviving landmark is skipped", async ({ page }) => {
  await boot(page);
  await swapAtCrossing(
    page,
    '<button id="b1" type="button">Enabled one</button>',
    '<button id="dis" type="button" disabled>Dis</button><button id="ok" type="button">Other</button>',
  );
  await cross(page, PHONE);
  expect((await active(page)).id).toBe("ok");
});

test("a visibility:hidden twin with the same name is skipped", async ({ page }) => {
  await boot(page);
  await swapAtCrossing(
    page,
    '<button id="b1" type="button">Twin</button>',
    '<div style="visibility:hidden"><button id="hid" type="button">Twin</button></div><button id="ok" type="button">Other</button>',
  );
  await cross(page, PHONE);
  expect((await active(page)).id).toBe("ok");
});

test("a same-named twin in another landmark placed AFTER the real one does not win", async ({ page }) => {
  await boot(page);
  await swapAtCrossing(
    page,
    '<button id="b1" type="button">Twin</button>',
    '<button id="real" type="button">Twin</button>',
    '<button id="decoy" type="button">Twin</button>',
  );
  await cross(page, PHONE);
  expect((await active(page)).id).toBe("real");
});

test("focus the app moved to a real control before the hand-off frame is not taken back", async ({ page }) => {
  await boot(page);
  await page.getByTestId("left-nav").getByRole("link", { name: "Library" }).focus();
  await page.evaluate(() => {
    window.matchMedia("(min-width: 1024px)").addEventListener("change", () => {
      queueMicrotask(() => document.querySelector<HTMLElement>("textarea")!.focus());
    });
  });
  await cross(page, PHONE);
  expect((await active(page)).tag).toBe("textarea");
});
