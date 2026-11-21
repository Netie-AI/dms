import { expect, test, type Page } from "@playwright/test";

/**
 * Ontology on an iPhone: the neighbourhood list replaces the ring canvas, the
 * unavailable state is honest, and a Space's derived ontology is readable.
 * The API is stubbed so the data is exact; every assertion is on what renders.
 */

const SHOTS = "/tmp/claude-0/-home-user/4e000c9a-b8cf-5fd7-a03e-e059c8322116/scratchpad/onto";

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

const NODES = ["customer", "order", "order_line", "product"].map((id) => ({
  id,
  label: id,
  description: `${id} rows`,
  primary_key: "id",
  property_count: 5,
  sensitive_count: 0,
  metric_count: 1,
  degree: 2,
}));
const EDGES = [
  ["order", "customer", "customer_id"],
  ["order_line", "order", "order_id"],
  ["order_line", "product", "product_id"],
  ["order", "product", "favourite_product_id"],
].map(([source, target, prop], i) => ({
  id: `e${i}`,
  source,
  target,
  source_property: prop,
  target_property: "id",
  cardinality: "many_to_one",
}));

async function stubOntology(page: Page, available: boolean) {
  await page.route("**/api/v1/ontology**", (route) => {
    const section = new URL(route.request().url()).pathname.split("/").pop() ?? "";
    if (!available) {
      return route.fulfill({
        json: {
          ok: false,
          section,
          error: "unreachable: ConnectError",
          hint: "Cortex is not answering on http://127.0.0.1:8010. Start the engine, then reload.",
        },
      });
    }
    const bodies: Record<string, unknown> = {
      ontology: {
        ok: true,
        pack: "test",
        counts: { object_types: 4, properties: 20, sensitive_properties: 0, link_types: 4, action_types: 0, action_tools: 0, action_events: 0, functions: 0, metrics: 0 },
        objects_without_metrics: [],
      },
      objects: {
        ok: true,
        object_types: NODES.map((n) => ({
          id: n.id, description: n.description, primary_key: "id", properties: [], property_count: 5, sensitive_count: 0, metric_ids: [],
        })),
      },
      links: {
        ok: true,
        link_types: EDGES.map((e) => ({
          id: e.id, from_object: e.source, from_property: e.source_property, to_object: e.target, to_property: "id", cardinality: e.cardinality,
        })),
      },
      actions: { ok: true, action_types: [] },
      functions: { ok: true, functions: [] },
      metrics: { ok: true, metrics: [] },
      graph: { ok: true, nodes: NODES, edges: EDGES },
    };
    return route.fulfill({ json: bodies[section] });
  });
}

async function openClientSide(page: Page, route: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Switch to operator mode" }).click();
  await page.evaluate((r) => {
    history.pushState({}, "", r);
    dispatchEvent(new PopStateEvent("popstate"));
  }, route);
}

// The tab strip scrolls sideways on purpose; nothing else may.
const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => {
    const over = (el: Element) => el.scrollWidth - el.clientWidth;
    const rest = [...document.querySelectorAll("main *")].filter(
      (e) => !e.closest("[role=tablist]") && !e.classList.contains("sr-only"),
    );
    return Math.max(over(document.documentElement), ...rest.map(over));
  });

async function shortControls(page: Page) {
  return page.evaluate(() =>
    [...document.querySelectorAll("button, input, select, a[href]")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth;
      })
      .map((el) => ({ label: (el.textContent || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 30), h: el.getBoundingClientRect().height }))
      .filter((c) => c.h < 43.5),
  );
}

async function smallText(page: Page, scope = "main") {
  return page.evaluate((sel) => {
    const out: string[] = [];
    for (const el of document.querySelectorAll(`${sel} *`)) {
      const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent?.trim());
      const r = el.getBoundingClientRect();
      if (!own || r.width === 0 || r.height === 0) continue;
      const px = parseFloat(getComputedStyle(el).fontSize);
      if (px < 12) out.push(`${el.textContent?.trim().slice(0, 30)} ${px}px`);
    }
    return out;
  }, scope);
}

test.describe("Ontology page at 390px", () => {
  test("Map is a tappable neighbourhood, not a canvas", async ({ page }) => {
    await stubOntology(page, true);
    await openClientSide(page, "/ontology");
    await expect(page.getByRole("heading", { level: 1, name: "Ontology" })).toBeVisible();

    // Tabs: one line, scrollable strip.
    const tabs = page.getByRole("tab");
    await expect(tabs).toHaveCount(5);
    const ys = await tabs.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().y)));
    expect(new Set(ys).size).toBe(1);

    await page.getByRole("tab", { name: "Map" }).click();
    await expect(page.locator("svg[aria-label]")).toHaveCount(0);
    const list = page.getByRole("button", { name: /^customer/ });
    for (const name of ["customer", "order", "order_line", "product"]) {
      await expect(page.getByRole("button", { name: new RegExp(`^${name}\\b`) }).first()).toBeVisible();
    }
    await expect(list).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/map-list.png` });

    await page.getByRole("button", { name: /^order\b(?!_)/ }).click();
    await expect(page.getByRole("heading", { level: 3, name: "order" })).toBeVisible();
    // Direct links of order: customer (via customer_id), order_line, product.
    for (const other of ["customer", "order_line", "product"]) {
      await expect(page.getByRole("button", { name: new RegExp(`^${other}\\b`) })).toBeVisible();
    }
    await expect(page.getByText("order.customer_id → customer.id · many_to_one")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/map-centred.png` });

    await page.getByRole("button", { name: /^order_line\b/ }).click();
    await expect(page.getByRole("heading", { level: 3, name: "order_line" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^product\b/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /^customer\b/ })).toHaveCount(0);

    expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(1);
    expect(await shortControls(page)).toEqual([]);
    expect(await smallText(page)).toEqual([]);

    // The other tabs hold the same floor.
    await page.getByRole("tab", { name: "Objects" }).click();
    await expect(page.getByText("order_line").first()).toBeVisible();
    expect(await shortControls(page)).toEqual([]);
    expect(await smallText(page)).toEqual([]);
    expect(await noHorizontalScroll(page)).toBeLessThanOrEqual(1);
  });

  test("desktop keeps the ring map", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await stubOntology(page, true);
    await page.goto("/ontology");
    await page.getByRole("tab", { name: "Map" }).click();
    await expect(page.locator("svg[aria-label]")).toBeVisible();
    await expect(page.getByRole("heading", { level: 3 })).toHaveCount(0);
  });

  test("an unreachable engine is one honest empty state", async ({ page }) => {
    await stubOntology(page, false);
    await openClientSide(page, "/ontology");
    const box = page.getByTestId("ontology-unavailable");
    await expect(box).toContainText("Ontology unavailable");
    await expect(box).toContainText("Cortex is not answering on http://127.0.0.1:8010");
    await expect(page.getByText(/No object type matches/)).toHaveCount(0);
    expect(await shortControls(page)).toEqual([]);
    expect(await smallText(page)).toEqual([]);
    await page.screenshot({ path: `${SHOTS}/unavailable.png` });
  });
});

const VIOLATION =
  "link orders->items: 3 child rows point at no parent row, so this join cannot be trusted";

const derived = {
  space_id: "s",
  derived: true,
  ontologies: [
    {
      version_id: "v1",
      version_status: "active",
      answers_asks: true,
      source: { kind: "postgresql", host: "db.internal", database: "shop", schema: "public" },
      measured_at: "2026-11-01T09:00:00+00:00",
      verified: false,
      objects: [
        { name: "bronze.customers", source_table: "public.customers", key: ["id"], attributes: [{ name: "id", type: "BIGINT" }, { name: "name", type: "VARCHAR" }], status: "verified", violations: [] },
        { name: "bronze.orders", source_table: "public.orders", key: ["id"], attributes: [{ name: "id", type: "BIGINT" }, { name: "customer_id", type: "BIGINT" }, { name: "total", type: "DOUBLE" }], status: "verified", violations: [] },
      ],
      links: [
        { name: "orders_customer_fk", from: "bronze.orders", from_columns: ["customer_id"], to: "bronze.customers", to_columns: ["id"], cardinality: "many_to_one", max_fanout: 1, status: "verified", violations: [] },
        { name: "orders_items_fk", from: "bronze.orders", from_columns: ["item_id"], to: "bronze.customers", to_columns: ["id"], cardinality: "many_to_one", max_fanout: 1, status: "unverified", violations: [{ check: "link_orphans", subject: "orders_items_fk", detail: VIOLATION }] },
      ],
      measures: [],
      measures_note: "no measures are derived; a question needing one abstains no_declared_measure until a steward declares it",
    },
  ],
};

test.describe("Space derived ontology at 390px", () => {
  test("shows counts, objects, links and the violation, never 'certified'", async ({ page }) => {
    await page.route("**/api/v1/spaces/*/ontology", (route) => route.fulfill({ json: derived }));
    await openClientSide(page, "/spaces");
    const card = page.getByTestId("space-ontology-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("2 objects · 2 links");
    await expect(card).toContainText("bronze.orders");
    await expect(card).toContainText("key id · 3 attributes");
    await expect(card).toContainText("bronze.orders → bronze.customers on customer_id = id");
    await expect(card).toContainText("bronze.orders → bronze.customers on item_id = id");
    await expect(card.getByText("unverified", { exact: true })).toHaveCount(1);
    await expect(card).toContainText(`Link orphans: ${VIOLATION}`);
    await expect(card).toContainText("Measures are declared by a steward, never derived.");
    expect((await card.innerText()).toLowerCase()).not.toContain("certified");
    expect(await smallText(page, "[data-testid=space-ontology-card]")).toEqual([]);
    const b = await card.boundingBox();
    expect(b!.x + b!.width).toBeLessThanOrEqual(390);
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${SHOTS}/space-card.png` });
  });

  test("nothing derived is a neutral empty state with the reason", async ({ page }) => {
    const reason = "no ontology has been derived for this Space; ingest a SQL source into it";
    await page.route("**/api/v1/spaces/*/ontology", (route) =>
      route.fulfill({ json: { space_id: "s", derived: false, ontologies: [], reason } }),
    );
    await openClientSide(page, "/spaces");
    const card = page.getByTestId("space-ontology-card");
    await expect(card).toContainText(reason);
    await expect(card).not.toContainText("objects ·");
    expect((await card.innerText()).toLowerCase()).not.toContain("certified");
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${SHOTS}/space-card-empty.png` });
  });
});
