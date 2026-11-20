/**
 * ONTO-PROFILE-03 (dms#516): the steward review screen, against the RUNNING DMS API.
 *
 * Nothing here fulfils a response. Setup posts to the real API (create a Space,
 * upload the five retail files of tests/test_onto_profile_01.py), and every
 * assertion is on the rendered page or on a request the page made. Two places
 * touch the network layer, and both still end at the real API:
 * - one approve request is HELD, then released unchanged, to prove the page
 *   shows no new status while the API has not answered;
 * - the 403 test re-points the page's ontology calls at a second real DMS API
 *   that runs without a steward grant (DMS_ACTOR_ROLE other than steward).
 *
 * Needs:
 *   UI dev server on PLAYWRIGHT_BASE_URL (default http://127.0.0.1:3000)
 *   DMS_E2E_API_URL              steward DMS API, default http://127.0.0.1:8090
 *   DMS_E2E_NON_STEWARD_API_URL  a DMS API started with DMS_ACTOR_ROLE=viewer
 * The gate is fail-closed, so the API needs Cortex up (apps/ui/README.md).
 * A missing piece fails the test. Nothing is skipped.
 *
 * The fixture files in e2e/fixtures/onto-profile-retail are the bytes of
 * `retail_files()` in tests/test_onto_profile_01.py (SEED 514), written out.
 * This spec uploads same-named files into a new Space on every run.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, "fixtures", "onto-profile-retail");
const SHOTS = path.join(HERE, "..", "test-results", "onto-review");
const FILES = ["customers.csv", "products.csv", "orders.csv", "order_items.csv", "shipments.csv", "audits.csv"];
const API = process.env.DMS_E2E_API_URL ?? "http://127.0.0.1:8090";
const NON_STEWARD_API = process.env.DMS_E2E_NON_STEWARD_API_URL ?? "";

type Item = {
  id: string;
  kind: "object" | "link";
  status: string;
  payload: {
    relation?: string;
    key?: string[];
    from_object?: string;
    from_columns?: string[];
    to_object?: string;
    to_columns?: string[];
  };
};

let spaceId = "";
/** file name -> landed table label, as the ingest route answered. */
let table: Record<string, string> = {};

async function createSpace(request: APIRequestContext, name: string): Promise<string> {
  const res = await request.post(`${API}/v1/spaces`, { data: { name } });
  expect(res.status(), await res.text()).toBe(201);
  return String((await res.json()).space.id);
}

async function apiItems(request: APIRequestContext, id: string): Promise<{ persisted: boolean; items: Item[] }> {
  const res = await request.get(`${API}/v1/ontology/data/spaces/${id}/items`);
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

function row(page: Page, id: string): Locator {
  return page.locator(`[data-testid="onto-review-item"][data-item-id="${id}"]`);
}

function keyItem(items: Item[], file: string, column: string): Item {
  const found = items.filter(
    (i) => i.kind === "object" && i.payload.relation === table[file] && i.payload.key?.join("+") === column,
  );
  expect(found, `one key item for ${file}.${column}`).toHaveLength(1);
  return found[0];
}

/**
 * Every cell value of the uploaded files, minus column names and short integers.
 * An all-digit value of 6 or more digits IS kept: audits.csv carries distinctive
 * ids (730101.., 884400117..) so a numeric value on the page cannot hide as a count.
 */
function cellValues(): string[] {
  const values = new Set<string>();
  const names = new Set<string>();
  for (const file of FILES) {
    const [header, ...lines] = readFileSync(path.join(FIXTURES, file), "utf8").trim().split("\n");
    for (const col of header.split(",")) names.add(col);
    for (const line of lines) for (const cell of line.split(",")) values.add(cell);
  }
  return [...values].filter((v) => !names.has(v) && !(/^\d+$/.test(v) && v.length < 6));
}

/**
 * No cell value in the visible text, an aria-label or a title of the review page.
 * Integers under 6 digits are left out of the list: a quantity "3" cannot be told from a count.
 */
async function expectNoCellValue(page: Page): Promise<void> {
  const root = page.getByTestId("onto-review-page");
  const text = [
    await root.innerText(),
    ...(await root.locator("[aria-label], [title]").evaluateAll((els) =>
      els.map((el) => `${el.getAttribute("aria-label") ?? ""} ${el.getAttribute("title") ?? ""}`),
    )),
  ].join("\n");
  expect(text).not.toMatch(/verif|certif/i);
  const values = cellValues();
  expect(values.length).toBeGreaterThan(800);
  // The check sees numeric values: if these were dropped again, a leaked id would pass.
  expect(values).toContain("730101");
  expect(values).toContain("884400117");
  const leaked = values.filter((v) => new RegExp(`(^|[^\\w])${escapeRe(v)}($|[^\\w])`).test(text));
  expect(leaked).toEqual([]);
}

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function measure(page: Page): Promise<{ items: Item[]; persisted: boolean; bounds: unknown }> {
  const answered = page.waitForResponse(
    (r) => r.url().includes("/v1/ontology/data/profile") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Measure this Space" }).click();
  const res = await answered;
  expect(res.status()).toBe(200);
  const body = await res.json();
  await expect(page.getByTestId("onto-review-bounds")).toBeVisible();
  return body;
}

/** The page scrolls inside the app shell, so grow the viewport to the content before a shot. */
async function shot(page: Page, name: string): Promise<void> {
  const size = page.viewportSize()!;
  const extra = await page.getByTestId("onto-review-page").evaluate((el) => {
    const scroller = el.closest(".overflow-y-auto") as HTMLElement | null;
    return scroller ? scroller.scrollHeight - scroller.clientHeight : 0;
  });
  await page.setViewportSize({ width: size.width, height: size.height + extra });
  await page.screenshot({ path: path.join(SHOTS, name), fullPage: true });
  await page.setViewportSize(size);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  spaceId = await createSpace(request, `ONTO-PROFILE-03 e2e ${Date.now()}`);
  // One batch, five parts named "files": the same request the #514 tests post.
  const form = new FormData();
  form.append("space_id", spaceId);
  for (const name of FILES) {
    form.append("files", new File([readFileSync(path.join(FIXTURES, name))], name, { type: "text/csv" }));
  }
  const res = await request.post(`${API}/v1/studio/ingest-batch`, { multipart: form });
  expect(res.status(), await res.text()).toBe(200);
  const receipt = await res.json();
  expect(receipt.ingested, JSON.stringify(receipt)).toBe(FILES.length);
  const landed: { file: string; table: string }[] = receipt.files;
  table = Object.fromEntries(landed.map((f) => [f.file, f.table]));
  expect(Object.keys(table).sort()).toEqual([...FILES].sort());
});

test.describe("ONTO-PROFILE-03 steward review, 1280px", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("measure, approve one, reject one, the 409, link usability, reload", async ({ page, request }) => {
    const calls: string[] = [];
    page.on("request", (r) => {
      const url = new URL(r.url());
      // Every ontology-data call, and every write the page makes to any route.
      if (url.pathname.includes("/v1/ontology/data/") || (url.pathname.startsWith("/api/") && r.method() !== "GET")) {
        calls.push(`${r.method()} ${url.pathname.replace(/^\/api/, "")}`);
      }
    });

    await page.goto(`/spaces/${spaceId}/ontology-review`);
    const root = page.getByTestId("onto-review-page");
    await expect(page.getByTestId("onto-review-summary")).toHaveText(
      "No proposal is stored for this Space yet.",
    );
    // Before any measurement there is nothing to approve.
    await expect(page.getByTestId("onto-review-approve")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-reject")).toHaveCount(0);

    // --- clause 1: measure, evidence in words ------------------------------------
    const body = await measure(page);
    expect(calls.filter((c) => c === "POST /v1/ontology/data/profile")).toHaveLength(1);
    const items = body.items;
    expect(items.length).toBeGreaterThanOrEqual(8);
    await expect(page.getByTestId("onto-review-item")).toHaveCount(items.length);
    for (const item of items) {
      await expect(row(page, item.id)).toHaveAttribute("data-status", "proposed");
      await expect(row(page, item.id).getByTestId("onto-review-status")).toHaveText("proposed");
    }

    await expectNoCellValue(page);

    const customerId = keyItem(items, "customers.csv", "customer_id");
    const customerName = keyItem(items, "customers.csv", "name");
    const orderId = keyItem(items, "orders.csv", "order_id");
    const shipmentId = keyItem(items, "shipments.csv", "shipment_id");
    const unitPrice = keyItem(items, "products.csv", "unit_price");

    await expect(row(page, orderId.id).getByTestId("onto-review-evidence")).toHaveText(
      "300 rows, 300 distinct, 0 empty",
    );
    await expect(row(page, customerId.id).getByTestId("onto-review-evidence")).toHaveText(
      "60 rows, 60 distinct, 0 empty",
    );
    await expect(row(page, unitPrice.id).getByTestId("onto-review-evidence")).toHaveText(
      "25 rows, 25 distinct, 0 empty",
    );

    const links = items.filter((i) => i.kind === "link");
    const shipLink = links.find(
      (i) => i.payload.from_object === table["shipments.csv"] && i.payload.to_object === table["orders.csv"],
    );
    expect(shipLink, "the shipments to orders link is proposed").toBeTruthy();
    await expect(row(page, shipLink!.id).getByTestId("onto-review-evidence")).toHaveText(
      "120 rows, 71 distinct values, 71 of 300 parent keys matched, largest fan-out 4, 0 empty",
    );

    // Per table: the orphan observation, the keyless table, their reasons in words.
    const ordersTable = page.locator(`[data-testid="onto-review-table"][data-table="${table["orders.csv"]}"]`);
    await expect(ordersTable.getByTestId("onto-review-observation")).toContainText(
      `5 rows of ${table["orders.csv"]}.customer_id point at a ${table["customers.csv"]}.customer_id that does not exist`,
    );
    await expect(ordersTable.getByTestId("onto-review-observation")).toContainText(
      "300 rows, 63 distinct values, 58 of 60 parent keys matched, largest fan-out 11",
    );
    const itemsTable = page.locator(
      `[data-testid="onto-review-table"][data-table="${table["order_items.csv"]}"]`,
    );
    await expect(itemsTable.getByTestId("onto-review-unkeyed")).toHaveText(
      "This table has no column that identifies a row. 4 columns and 6 pairs of columns were checked across 800 rows.",
    );
    await expect(itemsTable.getByTestId("onto-review-item")).toHaveCount(0);
    await expect(itemsTable.getByTestId("onto-review-observation")).toHaveCount(2);
    await expect(itemsTable.getByTestId("onto-review-observation").first()).toContainText(
      `${table["order_items.csv"]} has no column that identifies a row`,
    );
    await expect(page.getByTestId("onto-review-table")).toHaveCount(6);

    // Every stated bound: one line per leaf of the API's `bounds`.
    const bounds = page.getByTestId("onto-review-bounds").locator("li");
    await expect(bounds).toHaveCount(19);
    const boundsText = (await bounds.allInnerTexts()).join("\n");
    for (const want of [
      "At most 60 columns per table are measured.",
      "At most 20 columns per table are paired.",
      "Keys wider than two columns: not searched.",
      `${table["order_items.csv"]}: 6 pairs searched.`,
      "Links from a table to itself: not searched.",
      "At most 2000 column pairs are measured for links.",
      "Type rule: identical landed type, compared as landed, no cast or trim.",
      "at least 50% of its distinct values match",
      "Tables measured on truncated data: none.",
      "No measurement failed.",
    ]) {
      expect(boundsText).toContain(want);
    }
    const stated = (body.bounds as { link_search: Record<string, number> }).link_search;
    expect(boundsText).toContain(`${stated.pairs_measured} column pairs measured.`);
    expect(boundsText).toContain(
      `${stated.pairs_below_orphan_threshold} column pairs left out because too few values matched.`,
    );

    // --- clause 7: the in-memory notice follows what the API said ------------------
    const stored = await apiItems(request, spaceId);
    expect(body.persisted).toBe(stored.persisted);
    await expect(page.getByTestId("onto-review-memory-notice")).toHaveCount(stored.persisted ? 0 : 1);
    if (!stored.persisted) {
      await expect(page.getByTestId("onto-review-memory-notice")).toContainText("kept in memory");
      await expect(page.getByTestId("onto-review-memory-notice")).toContainText("lost when the DMS API restarts");
    }

    // --- clauses 3 and 10: the controls on the page, all of them -------------------
    const controls = root.locator("button, a, input, select, textarea, [role=button], [role=checkbox]");
    const controlNames = await controls.evaluateAll((els) =>
      els.map((el) => el.getAttribute("aria-label") ?? (el.textContent ?? "").trim()),
    );
    const allowed = /^(Measure this Space|Back to Spaces|(Approve|Reject) (key|link) .+)$/;
    for (const name of controlNames) expect(name).toMatch(allowed);
    const approveNames = controlNames.filter((c) => c.startsWith("Approve "));
    expect(approveNames).toHaveLength(items.length);
    expect(new Set(approveNames).size).toBe(items.length);
    expect(controlNames.filter((c) => c.startsWith("Reject "))).toHaveLength(items.length);
    await expect(root.locator("input, select, textarea, [role=checkbox]")).toHaveCount(0);
    await expect(root.getByRole("button", { name: /all|every|version|measure(?! this Space)/i })).toHaveCount(0);

    // --- clause 5: the link is not usable while its keys are not approved ----------
    await expect(row(page, shipLink!.id).getByTestId("onto-review-link-usability")).toContainText(
      "not usable until both keys are approved",
    );

    // --- clause 2: approve one. No status before the API answers. ------------------
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    const approveUrl = `**/v1/ontology/data/items/${customerId.id}/approve`;
    await page.route(approveUrl, async (route) => {
      await held;
      await route.continue(); // unchanged, to the real API
    });
    const before = calls.length;
    const approved = page.waitForResponse((r) => r.url().includes(`/items/${customerId.id}/approve`));
    await row(page, customerId.id).getByTestId("onto-review-approve").click();
    await expect(row(page, customerId.id).getByTestId("onto-review-approve")).toHaveText("Approving…");
    await page.waitForTimeout(400);
    await expect(row(page, customerId.id)).toHaveAttribute("data-status", "proposed");
    await expect(row(page, customerId.id).getByTestId("onto-review-status")).toHaveText("proposed");
    await expect(row(page, customerId.id).getByTestId("onto-review-approved-note")).toHaveCount(0);
    expect((await apiItems(request, spaceId)).items.find((i) => i.id === customerId.id)?.status).toBe("proposed");
    release();
    const approveRes = await approved;
    expect(approveRes.status()).toBe(200);
    expect((await approveRes.json()).status).toBe("approved");
    await page.unroute(approveUrl);

    // --- clause 6: "approved", re-measured at every question -----------------------
    await expect(row(page, customerId.id)).toHaveAttribute("data-status", "approved");
    await expect(row(page, customerId.id).getByTestId("onto-review-status")).toHaveText("approved");
    await expect(row(page, customerId.id).getByTestId("onto-review-approved-note")).toContainText(
      "re-measured at every question",
    );
    // One press, one request, one item. Nothing else changed, and nothing follows late.
    await page.waitForTimeout(500);
    expect(calls.slice(before)).toEqual([`POST /v1/ontology/data/items/${customerId.id}/approve`]);
    for (const item of items.filter((i) => i.id !== customerId.id)) {
      await expect(row(page, item.id)).toHaveAttribute("data-status", "proposed");
    }

    // --- clause 4: the second key of one table is refused by the API ---------------
    const conflict = page.waitForResponse((r) => r.url().includes(`/items/${customerName.id}/approve`));
    await row(page, customerName.id).getByTestId("onto-review-approve").click();
    expect((await conflict).status()).toBe(409);
    await expect(row(page, customerName.id).getByTestId("onto-review-item-notice")).toHaveText(
      `customer_id is already approved as the key of ${table["customers.csv"]}. A table has one approved key: reject that one first. This item stays proposed.`,
    );
    await expect(row(page, customerName.id)).toHaveAttribute("data-status", "proposed");
    await expect(row(page, customerName.id).getByTestId("onto-review-status")).toHaveText("proposed");
    await expectNoCellValue(page);

    // --- clause 2: reject one -------------------------------------------------------
    const beforeReject = calls.length;
    const rejected = page.waitForResponse((r) => r.url().includes(`/items/${unitPrice.id}/revoke`));
    // Two presses in the same tick (assistive tech, automation) still send one request.
    await row(page, unitPrice.id)
      .getByTestId("onto-review-reject")
      .evaluate((el) => {
        (el as HTMLButtonElement).click();
        (el as HTMLButtonElement).click();
      });
    const rejectRes = await rejected;
    expect(rejectRes.status()).toBe(200);
    expect((await rejectRes.json()).status).toBe("revoked");
    await expect(row(page, unitPrice.id)).toHaveAttribute("data-status", "revoked");
    await expect(row(page, unitPrice.id).getByTestId("onto-review-status")).toHaveText("rejected");
    await expect(row(page, unitPrice.id).getByTestId("onto-review-reject")).toHaveCount(0);
    await page.waitForTimeout(500);
    expect(calls.slice(beforeReject)).toEqual([`POST /v1/ontology/data/items/${unitPrice.id}/revoke`]);

    // --- clause 5: usable only once BOTH keys are approved --------------------------
    await row(page, shipmentId.id).getByTestId("onto-review-approve").click();
    await expect(row(page, shipmentId.id)).toHaveAttribute("data-status", "approved");
    await expect(row(page, shipLink!.id).getByTestId("onto-review-link-usability")).toHaveText(
      `not usable until both keys are approved. Not approved yet: order_id as the key of ${table["orders.csv"]}.`,
    );
    await row(page, orderId.id).getByTestId("onto-review-approve").click();
    await expect(row(page, orderId.id)).toHaveAttribute("data-status", "approved");
    await expect(row(page, shipLink!.id).getByTestId("onto-review-link-usability")).toHaveText(
      "Both keys are approved. This link itself is not approved yet, so it is not used.",
    );
    // The link itself is still only proposed: approving its keys approved nothing else.
    await expect(row(page, shipLink!.id)).toHaveAttribute("data-status", "proposed");

    // --- clauses 6 and 10: words and values -----------------------------------------
    await expectNoCellValue(page);

    await shot(page, "review-1280-cream.png");

    // --- clause 2: after a reload the same statuses, read from the items route -----
    const beforeReload = calls.length;
    await page.reload();
    await expect(page.getByTestId("onto-review-item")).toHaveCount(items.length);
    expect(calls.slice(beforeReload)).toContain(`GET /v1/ontology/data/spaces/${spaceId}/items`);
    expect(calls.slice(beforeReload).filter((c) => c.startsWith("POST"))).toEqual([]);
    const after = await apiItems(request, spaceId);
    const want: Record<string, string> = {
      [customerId.id]: "approved",
      [shipmentId.id]: "approved",
      [orderId.id]: "approved",
      [unitPrice.id]: "revoked",
      [customerName.id]: "proposed",
      [shipLink!.id]: "proposed",
    };
    for (const item of after.items) {
      if (item.id in want) expect(item.status).toBe(want[item.id]);
      await expect(row(page, item.id)).toHaveAttribute("data-status", item.status);
    }
    await expect(row(page, unitPrice.id).getByTestId("onto-review-status")).toHaveText("rejected");
    await expect(row(page, customerId.id).getByTestId("onto-review-approved-note")).toBeVisible();
    await expect(page.getByTestId("onto-review-summary")).toContainText(
      `${items.length - 4} proposed, 3 approved, 1 rejected`,
    );
    await expect(page.getByTestId("onto-review-stored-only")).toBeVisible();
    await expect(page.getByTestId("onto-review-memory-notice")).toHaveCount(after.persisted ? 0 : 1);
    await expectNoCellValue(page);
  });

  test("the Spaces page links each Space to its review", async ({ page }) => {
    await page.goto("/spaces");
    const spaceRow = page.locator("li", { has: page.getByText(spaceId, { exact: true }) });
    await spaceRow.getByTestId("space-ontology-review-link").click();
    await expect(page).toHaveURL(new RegExp(`/spaces/${spaceId}/ontology-review$`));
    await expect(page.getByRole("heading", { name: "Review keys and links" })).toBeVisible();
  });

  test("409 no_file_tables: says so and names where a SQL-source Space is shown", async ({ page, request }) => {
    const empty = await createSpace(request, `ONTO-PROFILE-03 empty ${Date.now()}`);
    await page.goto(`/spaces/${empty}/ontology-review`);
    const answered = page.waitForResponse((r) => r.url().includes("/v1/ontology/data/profile"));
    await page.getByRole("button", { name: "Measure this Space" }).click();
    const res = await answered;
    expect(res.status()).toBe(409);
    expect((await res.json()).detail.code).toBe("no_file_tables");
    const notice = page.getByTestId("onto-review-no-file-tables");
    await expect(notice).toContainText("no table landed from a file");
    await expect(notice).toContainText("SQL-source Space");
    await expect(notice).toContainText("Spaces page");
    await expect(page.getByTestId("onto-review-item")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-approve")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-reject")).toHaveCount(0);
    await expectNoCellValue(page);
  });

  test("a Measure answer for one Space never lands on another Space's page", async ({ page, request }) => {
    const other = await createSpace(request, `ONTO-PROFILE-03 other ${Date.now()}`);
    await page.goto(`/spaces/${spaceId}/ontology-review`);
    await expect(page.getByTestId("onto-review-item").first()).toBeVisible();
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/v1/ontology/data/profile**", async (route) => {
      await held;
      await route.continue(); // unchanged, to the real API
    });
    const answered = page.waitForResponse((r) => r.url().includes("/v1/ontology/data/profile"));
    await page.getByRole("button", { name: "Measure this Space" }).click();
    // In-app navigation to the other Space while the first Space's answer is held.
    await page.evaluate((url) => {
      window.history.pushState({}, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, `/spaces/${other}/ontology-review`);
    await expect(page.getByTestId("onto-review-summary")).toHaveText("No proposal is stored for this Space yet.");
    release();
    expect((await answered).status()).toBe(200);
    await page.waitForTimeout(500);
    await expect(page.getByTestId("onto-review-summary")).toHaveText("No proposal is stored for this Space yet.");
    await expect(page.getByTestId("onto-review-item")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-table")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-approve")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Measure this Space" })).toBeEnabled();
  });

  test("403 on an Approve press mid-session: the grant notice, no control, no status change", async ({ page, request }) => {
    expect(NON_STEWARD_API, "DMS_E2E_NON_STEWARD_API_URL must name a running non-steward DMS API").not.toBe("");
    await page.goto(`/spaces/${spaceId}/ontology-review`);
    const body = await measure(page);
    const productId = keyItem(body.items, "products.csv", "product_id");
    expect(productId.status).toBe("proposed");
    // Only this approve goes to the real non-steward API; its 403 is passed through.
    await page.route(`**/v1/ontology/data/items/${productId.id}/approve`, async (route) => {
      const url = new URL(route.request().url());
      const res = await route.fetch({ url: `${NON_STEWARD_API}${url.pathname.replace(/^\/api/, "")}` });
      await route.fulfill({ response: res });
    });
    const answered = page.waitForResponse((r) => r.url().includes(`/items/${productId.id}/approve`));
    await row(page, productId.id).getByTestId("onto-review-approve").click();
    expect((await answered).status()).toBe(403);
    await expect(page.getByTestId("onto-review-steward-required")).toContainText("A steward grant is needed");
    await expect(page.getByRole("button", { name: /Approve|Reject|Measure/ })).toHaveCount(0);
    expect((await apiItems(request, spaceId)).items.find((i) => i.id === productId.id)?.status).toBe("proposed");
    await expectNoCellValue(page);
  });

  test("403 steward_grant_required: says a grant is needed, no Approve or Reject", async ({ page }) => {
    expect(
      NON_STEWARD_API,
      "DMS_E2E_NON_STEWARD_API_URL must name a running DMS API without a steward grant",
    ).not.toBe("");
    const statuses: number[] = [];
    await page.route("**/api/v1/ontology/data/**", async (route) => {
      const url = new URL(route.request().url());
      // Same request, sent to the real non-steward DMS API. Its answer is passed through.
      const res = await route.fetch({ url: `${NON_STEWARD_API}${url.pathname.replace(/^\/api/, "")}${url.search}` });
      statuses.push(res.status());
      await route.fulfill({ response: res });
    });
    await page.goto(`/spaces/${spaceId}/ontology-review`);
    const notice = page.getByTestId("onto-review-steward-required");
    await expect(notice).toContainText("A steward grant is needed");
    expect(statuses).toContain(403);
    expect(statuses.every((s) => s === 403)).toBe(true);
    await expect(page.getByTestId("onto-review-item")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-approve")).toHaveCount(0);
    await expect(page.getByTestId("onto-review-reject")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Approve|Reject|Measure/ })).toHaveCount(0);
    await shot(page, "review-1280-no-grant.png");
  });
});

test.describe("ONTO-PROFILE-03 steward review, 390px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  for (const mode of ["cream", "graphite"] as const) {
    test(`every Approve and Reject is reachable (${mode})`, async ({ page }) => {
      await page.addInitScript((m) => window.localStorage.setItem("dms-theme", m), mode);
      await page.goto(`/spaces/${spaceId}/ontology-review`);
      await measure(page);
      const buttons = page.locator('[data-testid="onto-review-approve"], [data-testid="onto-review-reject"]');
      const total = await buttons.count();
      // The 1280px test left 3 approved, 1 rejected and at least 4 proposed items.
      expect(total).toBeGreaterThanOrEqual(12);
      for (let i = 0; i < total; i += 1) {
        const button = buttons.nth(i);
        await button.scrollIntoViewIfNeeded();
        await expect(button).toBeVisible();
        await expect(button).toBeEnabled();
        const box = await button.boundingBox();
        expect(box, `button ${i} has a box`).not.toBeNull();
        expect(box!.width).toBeGreaterThanOrEqual(44);
        expect(box!.height).toBeGreaterThanOrEqual(44);
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(390);
        // Reachable means a tap lands on it: nothing covers its centre.
        const hit = await button.evaluate((el) => {
          const r = el.getBoundingClientRect();
          const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return top === el || el.contains(top);
        });
        expect(hit, `button ${i} is not covered`).toBe(true);
      }
      const overflowX = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflowX).toBeLessThanOrEqual(1);
      await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
      await expectNoCellValue(page);
      await shot(page, `review-390-${mode}.png`);
    });
  }

  test("a slow Measure never overwrites a status the API already confirmed", async ({ page }) => {
    await page.goto(`/spaces/${spaceId}/ontology-review`);
    await expect(page.getByTestId("onto-review-item").first()).toBeVisible();
    // Pick an item the API will accept.
    // A key of a table that has no approved key yet, so the API will not refuse the approval with a 409.
    const target = page
      .locator('[data-testid="onto-review-table"]')
      .filter({ hasNot: page.locator('[data-kind="key"][data-status="approved"]') })
      .locator('[data-testid="onto-review-item"][data-kind="key"][data-status="proposed"]')
      .first();
    const id = await target.getAttribute("data-item-id");
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/v1/ontology/data/profile**", async (route) => {
      await held;
      await route.continue(); // unchanged, to the real API
    });
    const answered = page.waitForResponse((r) => r.url().includes("/v1/ontology/data/profile"));
    await page.getByRole("button", { name: "Measure this Space" }).click();
    // While Measure is held no press can start: the confirmed status cannot be raced.
    await expect(row(page, id!).getByTestId("onto-review-approve")).toBeDisabled();
    await expect(row(page, id!).getByTestId("onto-review-reject")).toBeDisabled();
    release();
    expect((await answered).status()).toBe(200);
    await expect(page.getByRole("button", { name: "Measure this Space" })).toBeEnabled();
    // Now approve; a later Measure keeps what the API confirmed.
    await row(page, id!).getByTestId("onto-review-approve").click();
    await expect(row(page, id!)).toHaveAttribute("data-status", "approved");
    await page.unroute("**/v1/ontology/data/profile**");
    await page.getByRole("button", { name: "Measure this Space" }).click();
    await expect(page.getByRole("button", { name: "Measure this Space" })).toBeEnabled();
    await expect(row(page, id!)).toHaveAttribute("data-status", "approved");
    await expectNoCellValue(page);
  });

  test("1280px graphite screenshot of the same state", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.addInitScript(() => window.localStorage.setItem("dms-theme", "graphite"));
    await page.goto(`/spaces/${spaceId}/ontology-review`);
    await measure(page);
    await expect(page.getByTestId("onto-review-item").first()).toBeVisible();
    await shot(page, "review-1280-graphite.png");
  });
});
