import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";
import { BADGE_COPY } from "../src/lib/badgeCopy";
import { SUGGESTED_QUESTIONS } from "../src/lib/fixtures";

/**
 * PHONE-PATH-01 (#513): the stranger path at 390x844, touch, mobile.
 * Upload a file through the real "+" control, ask about it, see a truthful scope.
 *
 * Rules this file keeps:
 *  - the upload goes through the control a phone user taps (file chooser), never an API call;
 *  - the ask response is never rewritten or stubbed: B2 and B4 read the envelope the API sent;
 *  - a demo-mode answer never satisfies B2 or B4;
 *  - B3 is expected RED until the EPIC-003 defects land (F-TBD-24, F-TBD-25). It is not
 *    skipped and not weakened. B4 is RED on a stack with no OpenVault and model key.
 *
 * Needs the local stack with the Cortex gate up (an ungated upload is refused with 403).
 * B2 and B4 need DMS_ASK_MODE=live. B2 runs the E1-E9 check in Python
 * (tests/test_phone_path_01.py); set DMS_PYTHON if `python` is not the DMS venv.
 */

const PHONE = { width: 390, height: 844 };
const TARGET = 44;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../..");
const PYTHON = process.env.DMS_PYTHON ?? "python";
const ASK_TIMEOUT = 150_000;

/** The uploaded file. B4 computes its expected figure from these rows. */
const ROWS = [
  { sku: "A", qty: 11, region: "North" },
  { sku: "B", qty: 22, region: "South" },
  { sku: "C", qty: 33, region: "North" },
];
const CSV = `sku,qty,region\n${ROWS.map((r) => `${r.sku},${r.qty},${r.region}`).join("\n")}\n`;
const TOTAL_QTY = ROWS.reduce((n, r) => n + r.qty, 0);
const QUESTION = "What is the total qty?";

type IngestFile = { file: string; ingested?: boolean; table?: string | null; reason?: string };
type Ingest = {
  ingested: number;
  need_attention?: number;
  summary?: string | null;
  chat_can_see_it?: boolean | null;
  files?: IngestFile[] | null;
};
type Envelope = {
  text: string;
  badge: string;
  abstained: boolean;
  ask_mode?: string;
  demo_fallback_used?: boolean;
  space_id?: string | null;
  values?: { value: number }[];
  rows?: Record<string, unknown>[];
  contributing_sources?: { container: string }[];
};
type Walk = { spaceId: string; spaceName: string; csvName: string; ingest: Ingest; tables: string[] };

let shared: Walk | null = null;

const squash = (s: string) => s.replace(/\s+/g, " ").trim();

async function noSidewaysScroll(page: Page) {
  const w = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(w[0], "the page must not scroll sideways").toBeLessThanOrEqual(w[1]);
}

/** A control a thumb can hit: at least 44px and fully inside the 390x844 viewport. */
async function tappable(loc: Locator, what: string) {
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  expect(b, `${what} is rendered`).toBeTruthy();
  expect(b!.width, `${what} width`).toBeGreaterThanOrEqual(TARGET);
  expect(b!.height, `${what} height`).toBeGreaterThanOrEqual(TARGET);
  expect(b!.x, `${what} left edge`).toBeGreaterThanOrEqual(0);
  expect(b!.y, `${what} top edge`).toBeGreaterThanOrEqual(0);
  expect(b!.x + b!.width, `${what} right edge`).toBeLessThanOrEqual(PHONE.width);
  expect(b!.y + b!.height, `${what} bottom edge`).toBeLessThanOrEqual(PHONE.height);
}

async function setMode(page: Page, mode: "cream" | "graphite") {
  const want = mode === "graphite" ? /Switch to operator mode/ : /Switch to ask mode/;
  const toggle = page.getByRole("button", { name: want });
  if (await toggle.count()) await toggle.tap();
  await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
}

/** Make the Space the active scope the way a person does: its card on Spaces. */
async function enterSpace(page: Page, w: Walk) {
  await page.goto("/spaces");
  const card = page.locator("li", { hasText: w.spaceName });
  await card.getByRole("button", { name: "Ask in this Space" }).tap();
  await expect(page.locator("#space-switcher")).toHaveValue(w.spaceId);
}

async function tab(page: Page, name: string) {
  await page.getByTestId("tab-bar").getByRole("link", { name, exact: true }).tap();
}

/**
 * New Space through the UI, then Studio, then the "+" control. Leaves the page on Studio
 * with the receipt rendered.
 */
async function walk(page: Page): Promise<Walk> {
  const stamp = `${Date.now()}`;
  const spaceName = `Stranger ${stamp}`;
  const csvName = `e2e_stranger_${stamp}.csv`;
  const csvPath = test.info().outputPath(csvName);
  fs.writeFileSync(csvPath, CSV);

  await page.goto("/spaces");
  await page.getByPlaceholder(/Q4 close/).fill(spaceName);
  const [created] = await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname.endsWith("/v1/spaces") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Create Space" }).tap(),
  ]);
  expect(created.status(), `create Space: ${await created.text()}`).toBe(201);
  const spaceId = ((await created.json()) as { space: { id: string } }).space.id;
  await expect(page.getByText(/^Space created/)).toBeVisible();

  const w: Walk = { spaceId, spaceName, csvName, ingest: { ingested: 0 }, tables: [] };
  // The new Space is listed only after a reload (hand walk, 2026-11-20).
  await enterSpace(page, w);
  // Studio is an Operate-mode tab. In Ask mode a phone has no control that leads to it.
  await setMode(page, "graphite");
  await tab(page, "Studio");
  await expect(page.locator("#space-switcher")).toHaveValue(spaceId);

  const add = page.getByRole("button", { name: "Add files" });
  await tappable(add, "Add files");
  const chooserP = page.waitForEvent("filechooser");
  await add.tap();
  const chooser = await chooserP;
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => new URL(r.url()).pathname.endsWith("/v1/studio/ingest") && r.request().method() === "POST",
    ),
    chooser.setFiles(csvPath),
  ]);
  expect(response.status(), `ingest: ${await response.text()}`).toBe(200);
  w.ingest = (await response.json()) as Ingest;
  w.tables = (w.ingest.files ?? []).filter((f) => f.ingested && f.table).map((f) => f.table!);
  shared = w;
  return w;
}

/**
 * The chip a person reads before the number: its label is not enough, its colours are the signal.
 * Resolves the theme variables the Badge uses and compares computed ink and fill, so a green chip
 * wearing the abstain label (or the reverse) fails.
 */
async function expectChipTone(chip: Locator, tone: "ok" | "warn" | "mute") {
  const vars = {
    ok: ["--color-badge-ok", "--color-accent-soft"],
    warn: ["--color-badge-warn", "--color-warn-soft"],
    mute: ["--color-badge-mute", "--color-paper-2"],
  } as const;
  const got = await chip.evaluate((el, all) => {
    const probe = (name: string, prop: "color" | "backgroundColor") => {
      const p = document.createElement("span");
      p.style[prop] = `var(${name})`;
      document.body.appendChild(p);
      const v = getComputedStyle(p)[prop];
      p.remove();
      return v;
    };
    const cs = getComputedStyle(el);
    const out: Record<string, { ink: string; fill: string }> = {};
    for (const [k, [ink, fill]] of Object.entries(all)) out[k] = { ink: probe(ink, "color"), fill: probe(fill, "backgroundColor") };
    return { ink: cs.color, fill: cs.backgroundColor, opacity: cs.opacity, want: out };
  }, vars);
  expect(got.opacity, "chip is not faded").toBe("1");
  expect({ ink: got.ink, fill: got.fill }, `chip carries the ${tone} tone`).toEqual(got.want[tone]);
  for (const other of ["ok", "warn", "mute"] as const) {
    if (other !== tone && got.want[other].fill !== got.want[tone].fill) {
      expect(got.fill, `chip is not the ${other} fill`).not.toBe(got.want[other].fill);
    }
  }
}

async function phonePage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({ viewport: PHONE, hasTouch: true, isMobile: true });
  return ctx.newPage();
}

/** One Space and one upload serve the scope tests. A worker restart walks again. */
async function sharedWalk(browser: Browser): Promise<Walk> {
  if (shared) return shared;
  const page = await phonePage(browser);
  try {
    return await walk(page);
  } finally {
    await page.context().close();
  }
}

/** Bronze tables the API lists for the Space: what the Space holds and can be asked about. */
async function tablesInSpace(page: Page, spaceId: string): Promise<string[]> {
  const res = await page.request.get(`/api/v1/studio/bronze?space_id=${spaceId}`);
  expect(res.ok(), `bronze list: ${res.status()}`).toBeTruthy();
  return ((await res.json()) as { table: string }[]).map((r) => r.table);
}

async function askInChat(page: Page, question: string): Promise<{ env: Envelope; sent: Record<string, unknown> }> {
  const box = page.getByRole("textbox", { name: "Ask about your data" });
  await box.fill(question);
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => new URL(r.url()).pathname.endsWith("/v1/chat/ask") && r.request().method() === "POST",
      { timeout: ASK_TIMEOUT },
    ),
    page.getByRole("button", { name: "Ask", exact: true }).tap(),
  ]);
  expect(response.status(), `ask: ${await response.text()}`).toBe(200);
  const env = (await response.json()) as Envelope;
  await expect(page.getByTestId("answer-message").last().getByTestId("answer-text")).toBeVisible();
  return { env, sent: response.request().postDataJSON() as Record<string, unknown> };
}

/** Tick the uploaded table in Studio's Sources and carry it to Chat. */
async function groundOnUpload(page: Page, w: Walk) {
  await enterSpace(page, w);
  await setMode(page, "graphite");
  await tab(page, "Studio");
  await page.getByTestId("studio-sources-toggle").tap();
  const files = page.getByTestId("studio-files");
  for (const table of w.tables) {
    const tick = files.getByRole("checkbox", { name: `Ground answers in ${table}` });
    if (!(await tick.count())) await files.getByRole("button", { name: /Bronze/ }).tap();
    await tick.check();
  }
  await page.getByTestId("studio-sources-close").tap();
  await page.getByRole("button", { name: /Ask about these/ }).tap();
  await expect(page.getByText(/^Grounded in \d+ files?$/)).toBeVisible();
}

test.describe("PHONE-PATH-01 stranger path at 390x844", () => {
  test.use({ viewport: PHONE, hasTouch: true, isMobile: true, actionTimeout: 15_000 });
  // A live ask may wait on a model call (the API allows Cortex 120s).
  test.setTimeout(240_000);

  test("B1 the runbook's first step (/studio by URL) reaches Add files on a phone", async ({ page }) => {
    await page.goto("/studio");
    await tappable(page.getByRole("button", { name: "Add files" }), "Add files");
    await noSidewaysScroll(page);
  });

  test("B1 a CSV picked through + gives a receipt that equals the ingest response", async ({ page }) => {
    const w = await walk(page);
    const body = w.ingest;
    expect(body.ingested, "the CSV is ingested").toBe(1);
    expect(w.tables).toHaveLength(1);
    // The upload landed in the Space that was active, and in no other.
    expect(await tablesInSpace(page, w.spaceId)).toEqual(w.tables);
    expect(await tablesInSpace(page, "cccccccc-cccc-cccc-cccc-cccccccccccc")).not.toContain(w.tables[0]);

    const receipt = page.getByTestId("ingest-receipt");
    await expect(receipt).toBeVisible();
    await receipt.scrollIntoViewIfNeeded();
    const text = squash(await receipt.innerText());
    expect(body.summary, "the response carries a summary").toBeTruthy();
    expect(text).toContain(squash(body.summary!));
    expect(Number(/(\d+) ingested/.exec(text)?.[1])).toBe(body.ingested);
    expect(Number(/(\d+) need attention/.exec(text)?.[1])).toBe(body.need_attention ?? 0);

    // One row per file: a landed file names its table, a refused one shows a dash.
    const rows = receipt.locator("tbody tr");
    await expect(rows).toHaveCount((body.files ?? []).length);
    const tableCells = await rows.locator("td:last-child").allInnerTexts();
    expect(tableCells.filter((t) => t.trim() !== "—")).toHaveLength(body.ingested);
    expect(tableCells.filter((t) => t.trim() === "—")).toHaveLength(body.need_attention ?? 0);
    await expect(rows.first().locator("td").first()).toHaveText(w.csvName);
    await expect(rows.first().locator("td").first()).toBeInViewport();
    await expect(rows.first().locator("td:last-child")).toHaveText(w.tables[0]);
    await expect(rows.first().getByTestId("receipt-row-reason"), "a landed file is not called refused").toHaveCount(0);

    const visibility = receipt.getByTestId("receipt-chat-visibility");
    if (body.chat_can_see_it === false) {
      await expect(visibility, "chat_can_see_it:false is said in words").toBeVisible();
      await expect(visibility).toContainText(/Chat/);
      await expect(visibility).toContainText(/not confirmed/i);
      await expect(visibility).toBeInViewport();
    } else {
      await expect(visibility).toHaveCount(0);
    }
    await noSidewaysScroll(page);
  });

  test("B2 a question about the file renders the unmodified envelope, grounded on the upload", async ({
    page,
    browser,
  }) => {
    const w = await sharedWalk(browser);
    await groundOnUpload(page, w);
    const { env, sent } = await askInChat(page, QUESTION);

    expect(sent.space_id, "the ask is sent for the Space that received the upload").toBe(w.spaceId);
    expect(sent.grounded_tables).toEqual(w.tables);
    expect(env.ask_mode, "B2 is never satisfied by a demo-mode answer: run with DMS_ASK_MODE=live").toBe("live");
    expect(env.demo_fallback_used ?? false, "no demo fallback").toBe(false);
    expect(env.space_id).toBe(w.spaceId);

    // E1-E9, the Space and the sources, checked by the product's own validator.
    const capture = test.info().outputPath("b2-capture.json");
    fs.writeFileSync(capture, JSON.stringify({ space_id: w.spaceId, upload_tables: w.tables, envelope: env }, null, 1));
    try {
      execFileSync(PYTHON, ["tests/test_phone_path_01.py", capture], { cwd: REPO, encoding: "utf8", stdio: "pipe" });
    } catch (e) {
      const err = e as { stdout?: string; stderr?: string; message: string };
      throw new Error(`envelope check failed: ${err.stdout ?? ""}${err.stderr ?? ""}${err.message}`);
    }

    const answer = page.getByTestId("answer-message").last();
    const rendered = squash(await answer.getByTestId("answer-text").innerText());
    expect(rendered.length).toBeGreaterThan(0);
    if (env.abstained) {
      // Accepted only as an abstain: its badge, its reason, and nothing that reads as an answer.
      expect(rendered).toBe(squash(env.text));
      const chip = answer.getByText(BADGE_COPY.ABSTAIN.label, { exact: true }).first();
      await expect(chip).toBeVisible();
      await expectChipTone(chip, BADGE_COPY.ABSTAIN.tone);
      for (const [kind, copy] of Object.entries(BADGE_COPY)) {
        if (kind !== "ABSTAIN") await expect(answer.getByText(copy.label, { exact: true })).toHaveCount(0);
      }
      await expect(answer.getByTestId("answer-text").locator("button"), "no figure").toHaveCount(0);
      await expect(answer.getByTestId("answer-chart")).toHaveCount(0);
      await expect(answer.getByTestId("rows-table")).toHaveCount(0);
    } else {
      // Not executed on a stack without OpenVault and a model key (see the PR).
      expect(squash(env.text)).toContain(rendered);
      const badge = BADGE_COPY[env.badge as keyof typeof BADGE_COPY];
      const chip = answer.getByText(badge.label, { exact: true }).first();
      await expect(chip).toBeVisible();
      await expectChipTone(chip, badge.tone);
      const sources = env.contributing_sources ?? [];
      expect(sources.length, "a non-abstained answer cites its sources").toBeGreaterThan(0);
      for (const s of sources) expect(w.tables, `source ${s.container} was created by the upload`).toContain(s.container);
      const rows = env.rows ?? [];
      if (rows.length) {
        await expect(answer.getByTestId("rows-table").locator("tbody tr")).toHaveCount(rows.length);
        const table = squash(await answer.getByTestId("rows-table").innerText());
        for (const key of Object.keys(rows[0])) expect(table).toContain(key);
      }
    }
  });

  test("B3 Chat for the new Space suggests no demo-warehouse question and counts its sources", async ({
    page,
    browser,
  }) => {
    const w = await sharedWalk(browser);
    await enterSpace(page, w);
    await tab(page, "Chat");
    await expect(page.getByText(`Scoped to ${w.spaceName}.`, { exact: false })).toBeVisible();
    const canAsk = await tablesInSpace(page, w.spaceId);
    expect(canAsk).toEqual(w.tables);

    expect(SUGGESTED_QUESTIONS.length, "the demo question list is the oracle").toBeGreaterThan(0);
    const shown = (await page.getByTestId("chat-scroll").getByRole("button").allInnerTexts()).map(squash);
    const demo = shown.filter((q) => (SUGGESTED_QUESTIONS as readonly string[]).includes(q));
    expect.soft(demo, "demo-warehouse questions suggested in a Space that holds only the upload").toEqual([]);

    const opener = page.getByTestId("source-panel-open");
    await expect.soft(opener.getByTestId("source-count"), "source count on the composer").toHaveText(
      String(canAsk.length),
    );
    await expect
      .soft(opener)
      .toHaveAccessibleName(`Sources: ${w.spaceName} · ${canAsk.length} source${canAsk.length === 1 ? "" : "s"}`);
  });

  test("B3 Library for the new Space lists and previews only the uploaded table", async ({ page, browser }) => {
    const w = await sharedWalk(browser);
    await enterSpace(page, w);
    await tab(page, "Library");
    const tree = page.getByTestId("library-tree");
    await expect(tree.getByText(w.tables[0], { exact: true })).toBeVisible();
    // Open every folder so a hidden demo table cannot pass unseen.
    for (let i = 0; i < 20; i++) {
      const closed = tree.locator('[role="treeitem"][aria-expanded="false"] > button');
      if (!(await closed.count())) break;
      await closed.first().tap();
    }
    const leaves = (
      await tree.locator('[role="treeitem"]:not([aria-expanded]) > button span.font-medium').allInnerTexts()
    ).map(squash);
    expect(leaves.length, "the tree lists at least the upload").toBeGreaterThan(0);
    expect.soft(leaves.filter((t) => !w.tables.includes(t)), "tables that the upload did not create").toEqual([]);
    await expect.soft(page.getByTestId("library-detail")).not.toContainText(/demo warehouse/i);
  });

  test("B3 Spaces shows the new Space's source count", async ({ page, browser }) => {
    const w = await sharedWalk(browser);
    await page.goto("/spaces");
    const canAsk = await tablesInSpace(page, w.spaceId);
    const card = page.locator("li", { hasText: w.spaceName });
    await expect(card.getByText(/^\d+ sources$/i)).toHaveText(new RegExp(`^${canAsk.length} sources$`, "i"));
  });

  test("B3 Spaces says the Space is not persisted when the API reports persisted:false", async ({
    page,
    browser,
  }) => {
    const w = await sharedWalk(browser);
    const api = (await (await page.request.get("/api/v1/spaces")).json()) as { persisted?: boolean };
    await page.goto("/spaces");
    await expect(page.locator("li", { hasText: w.spaceName })).toBeVisible();
    const banner = page.getByTestId("spaces-memory-banner");
    if (api.persisted === false) {
      await expect(banner).toBeVisible();
      await expect(banner).toContainText(/not persisted/i);
      await expect(banner).toBeInViewport();
    } else {
      await expect(banner).toHaveCount(0);
    }
    await noSidewaysScroll(page);
  });

  test("B4 a live answer about the file states the figure computed from the CSV", async ({ page, browser }) => {
    const w = await sharedWalk(browser);
    await groundOnUpload(page, w);
    const { env } = await askInChat(page, QUESTION);
    // RED here means UNVERIFIED on this stack, never "skipped": B4 needs live mode, OpenVault and a model key.
    expect(env.ask_mode, "B4 needs DMS_ASK_MODE=live; a demo answer never stands in").toBe("live");
    expect(env.demo_fallback_used ?? false).toBe(false);
    expect(env.abstained, `B4 needs a non-abstained answer; got: ${env.text}`).toBe(false);
    expect((env.values ?? []).map((v) => v.value)).toContain(TOTAL_QTY);
    for (const s of env.contributing_sources ?? []) expect(w.tables).toContain(s.container);
    const figure = page.getByTestId("answer-message").last().getByTestId("answer-text").locator("button");
    expect((await figure.allInnerTexts()).map((t) => Number(t.replace(/[^\d.-]/g, "")))).toContain(TOTAL_QTY);
  });

  test("B5 the file picker does not offer .tsv", async ({ page }) => {
    await page.goto("/studio");
    const chooserP = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Add files" }).tap();
    const accept = (await (await chooserP).element().getAttribute("accept")) ?? "";
    const kinds = accept.split(",").map((k) => k.trim()).filter(Boolean);
    expect(kinds.length, "the picker filters by kind").toBeGreaterThan(0);
    expect(kinds).not.toContain(".tsv");
    // Every kind it does offer is ingested: tests/test_phone_path_01.py reads this same list.
    expect(kinds).toContain(".csv");
  });

  test("B5 a .tsv that arrives through Folder is shown as not ingested, with its reason", async ({ page }) => {
    const dir = test.info().outputPath("folder");
    fs.mkdirSync(dir, { recursive: true });
    const name = `e2e_stranger_${Date.now()}.tsv`;
    fs.writeFileSync(path.join(dir, name), "sku\tqty\tregion\nA\t11\tNorth\nB\t22\tSouth\n");

    await page.goto("/studio");
    const folder = page.getByRole("button", { name: "Folder" });
    await tappable(folder, "Folder");
    const chooserP = page.waitForEvent("filechooser");
    await folder.tap();
    const chooser = await chooserP;
    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => new URL(r.url()).pathname.endsWith("/v1/studio/ingest") && r.request().method() === "POST",
      ),
      chooser.setFiles(dir),
    ]);
    expect(response.status(), `ingest: ${await response.text()}`).toBe(200);
    const body = (await response.json()) as Ingest;
    const file = (body.files ?? []).find((f) => f.file.endsWith(name));
    expect(file, "the response names the .tsv").toBeTruthy();
    expect(file!.ingested, "a .tsv is not ingested").toBe(false);
    expect(file!.table ?? null).toBeNull();
    expect(file!.reason, "the refusal has a reason").toBeTruthy();
    expect(body.ingested).toBe(0);

    const receipt = page.getByTestId("ingest-receipt");
    await expect(receipt).toBeVisible();
    await receipt.scrollIntoViewIfNeeded();
    const text = squash(await receipt.innerText());
    expect(Number(/(\d+) ingested/.exec(text)?.[1])).toBe(0);
    expect(Number(/(\d+) need attention/.exec(text)?.[1])).toBe(1);
    const row = receipt.locator("tbody tr", { hasText: name });
    await expect(row).toHaveCount(1);
    await expect(row.locator("td").nth(2)).toContainText(file!.reason!);
    await expect(row.locator("td:last-child")).toHaveText("—");
    // The Reason column sits off screen at 390px, so the reason is also under the file name.
    const why = row.getByTestId("receipt-row-reason");
    await expect(why).toContainText("Not ingested");
    await expect(why).toContainText(file!.reason!);
    await expect(why).toBeInViewport({ ratio: 1 });
    await expect(receipt.getByTestId("receipt-chat-visibility")).toHaveText(
      "Nothing was ingested, so Chat cannot see this upload.",
    );
    await noSidewaysScroll(page);
  });
});
