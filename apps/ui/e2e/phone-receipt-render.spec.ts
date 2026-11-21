import fs from "node:fs";
import { expect, test, type Page } from "@playwright/test";

/**
 * PHONE-PATH-01 (#513), checker findings on the Studio receipt lines at 390x844.
 *
 * This is a RENDERER spec: the ingest response is crafted with page.route so that every wording
 * branch can be reached on any stack (the stranger-path spec needs the Cortex gate and cannot make
 * the API say "chat_can_see_it: true" or a detail that ends in a full stop). The file still goes
 * through the real "+" file chooser. The ask path is not touched here, and nothing here satisfies B2.
 *
 * What the person reads must be: said once (no "..", no repeated clause), visible (opacity 1, not
 * hidden, ink that meets 4.5:1 on its own background), inside the 390px viewport, and announced
 * (role=status).
 */

const PHONE = { width: 390, height: 844 };
const MODES = ["cream", "graphite"] as const;

type Body = Record<string, unknown>;

const landed = (over: Body = {}): Body => ({
  files_seen: 1,
  ingested: 1,
  quarantined: 0,
  need_attention: 0,
  reasons: [],
  ingest_id: "ing-1",
  summary: "1 file ingested.",
  per_class: null,
  files: [{ file: "sales.csv", classification: "csv", table: "sales", ingested: true }],
  serving_sync: "not_attempted",
  serving_sync_detail: null,
  chat_can_see_it: false,
  ...over,
});

/** Studio is an Operate-mode page (graphite). Cream is checked on the same receipt through the URL. */
async function upload(page: Page, mode: (typeof MODES)[number], body: Body, name = "sales.csv") {
  await page.addInitScript((m) => localStorage.setItem("dms-theme", m), mode);
  await page.route("**/api/v1/studio/ingest", (route) => route.fulfill({ json: body }));
  await page.goto("/studio");
  await page.waitForLoadState("networkidle");
  const file = test.info().outputPath(name);
  fs.writeFileSync(file, "a,b\n1,2\n");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add files" }).click();
  await (await chooser).setFiles(file);
  await expect(page.getByTestId("ingest-receipt")).toBeVisible();
}

/** Two files through the real chooser: the batch route. */
async function uploadMany(page: Page, mode: (typeof MODES)[number], body: Body) {
  await page.addInitScript((m) => localStorage.setItem("dms-theme", m), mode);
  await page.route("**/api/v1/studio/ingest-batch", (route) => route.fulfill({ json: body }));
  await page.goto("/studio");
  await page.waitForLoadState("networkidle");
  const files = ["one.csv", "two.csv"].map((n) => {
    const f = test.info().outputPath(n);
    fs.writeFileSync(f, "a,b\n1,2\n");
    return f;
  });
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add files" }).click();
  await (await chooser).setFiles(files);
  await expect(page.getByTestId("ingest-receipt")).toBeVisible();
}

/** Ink contrast and visibility of the element, read from computed styles up the ancestor chain. */
async function look(page: Page, testId: string) {
  return page.getByTestId(testId).first().evaluate((el) => {
    type C = { r: number; g: number; b: number; a: number };
    const parse = (s: string): C => {
      const m = /rgba?\(([^)]+)\)/.exec(s);
      if (!m) return { r: 0, g: 0, b: 0, a: 0 };
      const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 };
    };
    const over = (f: C, b: C): C => ({
      r: f.r * f.a + b.r * (1 - f.a),
      g: f.g * f.a + b.g * (1 - f.a),
      b: f.b * f.a + b.b * (1 - f.a),
      a: 1,
    });
    const lum = (c: C) => {
      const t = [c.r, c.g, c.b].map((x) => {
        x /= 255;
        return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2];
    };
    // Opacity multiplies up the chain; the background is composited from the root down.
    let opacity = 1;
    let hidden = false;
    const chain: Element[] = [];
    for (let e: Element | null = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      opacity *= parseFloat(cs.opacity);
      if (cs.visibility === "hidden" || cs.display === "none") hidden = true;
      chain.push(e);
    }
    let bg: C = { r: 255, g: 255, b: 255, a: 1 };
    for (const e of chain.reverse()) bg = over(parse(getComputedStyle(e).backgroundColor), bg);
    const fg0 = parse(getComputedStyle(el).color);
    const fg = over({ ...fg0, a: fg0.a * opacity }, bg);
    const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
    const r = el.getBoundingClientRect();
    return {
      ratio: (hi + 0.05) / (lo + 0.05),
      opacity,
      hidden,
      textAlpha: fg0.a,
      left: r.left,
      right: r.right,
      fontSize: parseFloat(getComputedStyle(el).fontSize),
      text: (el.textContent ?? "").replace(/\s+/g, " ").trim(),
      role: el.getAttribute("role"),
    };
  });
}

for (const mode of MODES) {
  test.describe(`receipt lines at 390x844, ${mode}`, () => {
    test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

    test("a detail that ends in a full stop is not followed by a second one", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "Serving sync did not run." }));
      const v = await look(page, "receipt-chat-visibility");
      expect(v.text).toBe("Not confirmed that Chat can see this upload: Serving sync did not run.");
      expect(v.text).not.toMatch(/\.\./);
    });

    test("a detail with no full stop gets exactly one", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "sync not attempted" }));
      expect((await look(page, "receipt-chat-visibility")).text).toBe(
        "Not confirmed that Chat can see this upload: sync not attempted.",
      );
    });

    test("no detail: the line still says it plainly, once", async ({ page }) => {
      await upload(page, mode, landed());
      expect((await look(page, "receipt-chat-visibility")).text).toBe("Not confirmed that Chat can see this upload.");
    });

    test("when the API says Chat can see the upload, the line is absent", async ({ page }) => {
      await upload(page, mode, landed({ chat_can_see_it: true, serving_sync: "ok" }));
      await expect(page.getByTestId("receipt-chat-visibility")).toHaveCount(0);
    });

    test("when the API says nothing about it (null), the line is absent", async ({ page }) => {
      await upload(page, mode, landed({ chat_can_see_it: null }));
      await expect(page.getByTestId("receipt-chat-visibility")).toHaveCount(0);
    });

    test("nothing ingested: it says Chat cannot see the upload", async ({ page }) => {
      await upload(
        page,
        mode,
        landed({
          ingested: 0,
          need_attention: 1,
          summary: "0 files ingested.",
          files: [{ file: "x.csv", classification: "csv", table: null, ingested: false, reason: "empty_file", fix: "Add a header row." }],
        }),
      );
      expect((await look(page, "receipt-chat-visibility")).text).toBe("Nothing was ingested, so Chat cannot see this upload.");
    });

    test("a detail that already ends in ! or an ellipsis is not given another mark", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "sync failed!" }));
      expect((await look(page, "receipt-chat-visibility")).text).toBe("Not confirmed that Chat can see this upload: sync failed!");
    });

    test("a detail with no spaces, 600 characters long, wraps inside the screen and nothing scrolls sideways", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "x".repeat(600) }));
      const v = await look(page, "receipt-chat-visibility");
      expect(v.right).toBeLessThanOrEqual(PHONE.width);
      // The Studio scroller, not only the document: a wide line scrolls the pane sideways.
      const wide = await page.getByTestId("ingest-receipt").evaluate((el) => {
        const bad: string[] = [];
        for (let e: Element | null = el; e; e = e.parentElement) {
          if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== "visible") bad.push(`${e.tagName} ${e.scrollWidth}>${e.clientWidth}`);
        }
        return bad;
      });
      expect(wide, "an ancestor of the receipt scrolls sideways").toEqual([]);
    });

    test("characters that reorder text are not passed through", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "ok\u202Esync" }));
      expect((await look(page, "receipt-chat-visibility")).text).toBe("Not confirmed that Chat can see this upload: oksync.");
    });

    test("the receipt is on screen after the upload without the person scrolling to it", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "Serving sync did not run." }));
      await expect(page.getByTestId("ingest-receipt")).toBeInViewport();
      await expect(page.getByTestId("receipt-summary")).toBeInViewport({ ratio: 1 });
    });

    test("the summary is exactly what the API said, readable, and not faded", async ({ page }) => {
      await upload(page, mode, landed({ summary: "1 file ingested into sales." }));
      const v = await look(page, "receipt-summary");
      expect(v.text).toBe("1 file ingested into sales.");
      expect(v.opacity).toBe(1);
      expect(v.textAlpha).toBe(1);
      expect(v.ratio).toBeGreaterThanOrEqual(4.5);
      expect(v.fontSize).toBeGreaterThanOrEqual(12);
    });

    test("with no summary the receipt counts for itself, in the right places", async ({ page }) => {
      await upload(page, mode, landed({ summary: null, files_seen: 3, ingested: 2, need_attention: 1, files: null }));
      expect((await look(page, "receipt-summary")).text).toBe("Receipt · 3 files · 2 ingested · 1 need attention");
    });

    test("two files take the batch route and each gets its row", async ({ page }) => {
      await uploadMany(
        page,
        mode,
        landed({
          files_seen: 2,
          ingested: 1,
          need_attention: 1,
          files: [
            { file: "one.csv", classification: "csv", table: "one", ingested: true },
            { file: "two.csv", classification: "csv", table: null, ingested: false, reason: "empty_file", fix: "Add a header row." },
          ],
        }),
      );
      const rows = page.getByTestId("ingest-receipt").locator("tbody tr");
      await expect(rows).toHaveCount(2);
      await expect(rows.nth(0).locator("td").first()).toHaveText("one.csv");
      await expect(rows.nth(1).locator("td").first()).toContainText("two.csv");
      await expect(rows.nth(1).getByTestId("receipt-row-reason")).toHaveCount(1);
      await expect(rows.nth(0).getByTestId("receipt-row-reason")).toHaveCount(0);
    });

    test("a row the API marks ingested is never called refused, even with no table name", async ({ page }) => {
      await upload(
        page,
        mode,
        landed({ files: [{ file: "sales.csv", classification: "csv", table: null, ingested: true, reason: "header_and_types_ok", fix: "Ready for bronze ingest as-is." }] }),
      );
      await expect(page.getByTestId("receipt-row-reason")).toHaveCount(0);
    });

    test("the visibility line is announced, visible, readable and inside the screen", async ({ page }) => {
      await upload(page, mode, landed({ serving_sync_detail: "Serving sync did not run." }));
      const v = await look(page, "receipt-chat-visibility");
      expect(v.role, "announced to a screen reader").toBe("status");
      expect(v.hidden).toBe(false);
      expect(v.opacity, "not faded out").toBe(1);
      expect(v.textAlpha, "ink is not transparent").toBe(1);
      expect(v.ratio, "ink on its own background").toBeGreaterThanOrEqual(4.5);
      expect(v.left).toBeGreaterThanOrEqual(0);
      expect(v.right).toBeLessThanOrEqual(PHONE.width);
    });

    test("a refused file shows its reason under its name: readable and inside the screen", async ({ page }) => {
      await upload(
        page,
        mode,
        landed({
          ingested: 0,
          need_attention: 1,
          files: [
            {
              file: "a_rather_long_name_for_a_tab_file_that_goes_on.tsv",
              classification: "tsv",
              table: null,
              ingested: false,
              reason: "unsupported_kind",
              fix: "Save it as .csv and add it again.",
            },
          ],
        }),
        "a_rather_long_name_for_a_tab_file_that_goes_on.tsv",
      );
      const r = await look(page, "receipt-row-reason");
      expect(r.text).toBe("Not ingested: unsupported_kind \u2014 Save it as .csv and add it again.");
      expect(r.fontSize, "readable size").toBeGreaterThanOrEqual(12);
      expect(r.hidden).toBe(false);
      expect(r.opacity).toBe(1);
      expect(r.textAlpha).toBe(1);
      expect(r.ratio).toBeGreaterThanOrEqual(4.5);
      expect(r.left).toBeGreaterThanOrEqual(0);
      expect(r.right).toBeLessThanOrEqual(PHONE.width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
    });
  });
}

test.describe("receipt lines at lg are what they were", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("no phone-only line shows on a desktop receipt", async ({ page }) => {
    await upload(page, "graphite", landed({ serving_sync_detail: "Serving sync did not run." }));
    // Present in the DOM for the phone, but hidden at lg (lg:hidden): a person reads nothing extra.
    const v = await look(page, "receipt-chat-visibility");
    expect(v.hidden).toBe(true);
  });

  test("a refused file's reason is not repeated under its name at lg (the Reason column has it)", async ({ page }) => {
    await upload(
      page,
      "graphite",
      landed({
        ingested: 0,
        need_attention: 1,
        reasons: [{ file: "x.tsv", reason: "unsupported_kind", fix: "Save it as .csv." }],
        files: [{ file: "x.tsv", classification: "tsv", table: null, ingested: false, reason: "unsupported_kind", fix: "Save it as .csv." }],
      }),
      "x.tsv",
    );
    expect((await look(page, "receipt-row-reason")).hidden).toBe(true);
    // The Reason column carries it instead, and a person can see it.
    const cell = page
      .getByTestId("ingest-receipt")
      .locator("tbody tr")
      .first()
      .locator("td")
      .filter({ hasText: "unsupported_kind" })
      .filter({ hasNot: page.getByTestId("receipt-row-reason") });
    await cell.scrollIntoViewIfNeeded();
    await expect(cell).toBeVisible();
    expect(await look(page, "receipt-summary")).toMatchObject({ hidden: false });
  });
});
