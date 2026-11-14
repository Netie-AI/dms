import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * IPHONE-ANSWER-ORDER (PR #510 follow-up). On a phone the answer used to be reordered
 * with CSS `order`: the figure first, the tools strip after it. That moves only the
 * eyes. Tab order and a screen reader follow the DOM, so they started on the tools.
 * WCAG 1.3.2 Meaningful Sequence and 2.4.3 Focus Order: at every width the DOM order
 * of the controls and headings in an answer must equal the order they are drawn in.
 *
 * Three more phone rules ride along. An abstained answer has no rows and no executed
 * SQL, so the tools that act on rows or SQL are not offered. The swipeable tools strip
 * says it scrolls. And desktop stays what it was: the 1280px values below were read
 * from the page BEFORE the change (same mocked envelopes, so they do not move with
 * the data), as boxes relative to the answer card.
 *
 * Measured before the change, the controls whose DOM position differed from their
 * drawn position at 390x844 (both modes, both answers): the whole tools strip came
 * before the figure button and the Details summary in the DOM, and after them on
 * screen. Demo: 11 tools before "RM 25,654.50" and "Details". Abstained: 9 tools
 * before the 4 suggestion chips, the 2 links and "Details". At 1280 the order was
 * already right.
 */

const PHONE = { width: 390, height: 844 };
const SE = { width: 375, height: 667 };
const DESKTOP = { width: 1280, height: 800 };
const TARGET = 44;
const MODES = ["cream", "graphite"] as const;
type Mode = (typeof MODES)[number];
type Kind = "demo" | "abstain";

const DEMO = {
  "answer_id": "ans_demo_revenue",
  "text": "Total outbound revenue was RM 25,654.50.",
  "values": [
    {
      "id": "v_rev",
      "value": 25654.5,
      "unit": "MYR",
      "label": "Total revenue"
    }
  ],
  "badge": "L2_VALIDATED",
  "abstained": false,
  "sql_used": "SELECT COALESCE(SUM(quantity_kg * unit_cost_myr), 0)::DOUBLE AS revenue_myr FROM transactions WHERE txn_type = 'outbound'",
  "assumptions": [
    "outbound transactions only",
    "revenue = quantity_kg × unit_cost_myr",
    "demo warehouse — not a live Cortex answer"
  ],
  "as_of": "2026-11-14T03:00:00Z",
  "contributing_sources": [
    {
      "ref_id": "ref_txn",
      "container": "transactions",
      "kind": "sql",
      "row_count": 10,
      "contribution": 1,
      "origin_uri": "duckdb://dms_demo/transactions",
      "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
      "extracted_at": null,
      "source_kind": null
    },
    {
      "ref_id": "ref_loc",
      "container": "locations",
      "kind": "sql",
      "row_count": 3,
      "contribution": 0,
      "origin_uri": "duckdb://dms_demo/locations",
      "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
      "extracted_at": null,
      "source_kind": null
    }
  ],
  "drillthrough_token": "demo_no_drillthrough",
  "audit_id": "ans_demo_revenue",
  "ask_mode": "demo",
  "demo_fallback_used": false,
  "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "rows": [
    {
      "metric": "total_revenue",
      "revenue_myr": 25654.5
    }
  ],
  "chart": {
    "kind": "bar",
    "x": "metric",
    "y": "revenue_myr",
    "title": "Total outbound revenue"
  },
  "suggestions": [
    "What was total revenue?",
    "Divide the revenue by 5",
    "Top 5 selling SKUs by revenue",
    "Show warehouse capacity utilisation",
    "Which SKUs are below reorder level?",
    "List active alerts across the warehouse network"
  ],
  "route": null,
  "audit_receipt": {
    "include": {
      "status": "rows",
      "row_count": 1,
      "rows": [
        {
          "metric": "total_revenue",
          "revenue_myr": 25654.5
        }
      ],
      "why": "executed result rows that produced the certified figures (query grain / LIMIT); not a claim of full-table completeness"
    },
    "exclude": {
      "status": "filters",
      "reasons": [
        {
          "kind": "where",
          "detail": "txn_type = 'outbound'"
        }
      ],
      "why": "extracted from executed SQL WHERE/HAVING/FILTER; grain-level exclusions not claimed"
    },
    "unsure": {
      "status": "none",
      "abstained": false,
      "badge": "L2_VALIDATED",
      "why": "not abstained; badge=L2_VALIDATED"
    }
  }
};

const ABSTAIN = {
  "answer_id": "ans_gen01_abstain",
  "text": "I cannot certify an ontology-grounded query for that question (gap: insights_bearer_missing), so I am not executing one.",
  "values": [],
  "badge": "ABSTAIN",
  "abstained": true,
  "sql_used": null,
  "assumptions": [
    "GEN-01: insights_bearer_missing"
  ],
  "as_of": "2026-11-14T03:00:00Z",
  "contributing_sources": [],
  "drillthrough_token": null,
  "audit_id": "ans_gen01_abstain",
  "ask_mode": "live",
  "demo_fallback_used": false,
  "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "rows": [],
  "chart": null,
  "suggestions": [],
  "route": "generated",
  "audit_receipt": {
    "include": {
      "status": "na",
      "row_count": 0,
      "rows": [],
      "why": "N/A: abstained; no include set"
    },
    "exclude": {
      "status": "na",
      "reasons": [],
      "why": "N/A: abstained; no exclude receipt"
    },
    "unsure": {
      "status": "abstain",
      "abstained": true,
      "badge": "ABSTAIN",
      "why": "GEN-01: insights_bearer_missing"
    }
  },
  "reconfirm": {
    "reason_code": "insights_bearer_missing",
    "reason_text": "I cannot certify an ontology-grounded query for that question (gap: insights_bearer_missing), so I am not executing one.",
    "suggested_question": "",
    "token": "rcfmdawvpcwzmcpsvuemsqgsmwirtca"
  }
};

const ENVELOPES: Record<Kind, Record<string, unknown>> = { demo: DEMO, abstain: ABSTAIN };

const PHONE_ABSTAIN_TOOLS = ["Share answer", "Trust runs", "⟨audit⟩"];
const ALL_ABSTAIN_TOOLS = [
  "⟨sql⟩",
  "Download Excel",
  "Power BI",
  "Superset",
  "Share answer",
  "Check accuracy",
  "Trust runs",
  "⟨sources⟩",
  "⟨audit⟩",
];
const DEMO_TOOLS = [
  "⟨sql⟩",
  "Download CSV",
  "Download Excel",
  "Power BI",
  "Superset",
  "Share answer",
  "Check accuracy",
  "Trust runs",
  "Copilot prompts",
  "Show me why",
  "⟨audit⟩",
];

/** [tag, name, x, y, width, height] relative to the answer card, in DOM order. */
type Ref = {
  article: [number, number];
  outline: string[];
  focus: [string, string, number, number, number, number][];
};
const REF_1280: Record<string, Ref> = {
  "demo-cream": {
    article: [768, 1480],
    outline: ["div::generated — check ", "div:audit-receipt:Include / exclude ", "p:answer-text:Total outbound rev", "div:rows-block:#metricrevenue_myr", "div:answer-chart:Total outbound rev", "details:answer-details:Detailsoutbound tr"],
    focus: [
      ["button", "generated — check sources", 17, 17, 188, 24],
      ["button", "⟨sql⟩", 379, 17, 33, 24],
      ["button", "Download CSV", 420, 17, 108, 24],
      ["button", "Download Excel", 536, 17, 114, 24],
      ["button", "Power BI", 658, 17, 63, 24],
      ["button", "Superset", 17, 49, 65, 24],
      ["button", "Share answer", 90, 49, 96, 24],
      ["button", "Check accuracy", 194, 49, 114, 24],
      ["a", "Trust runs", 316, 53, 53, 16],
      ["button", "Copilot prompts", 377, 49, 118, 24],
      ["button", "Show me why", 503, 49, 100, 24],
      ["a", "⟨audit⟩", 611, 53, 37, 16],
      ["button", "RM 25,654.50", 235, 194, 105, 29],
      ["g", "total_revenue: 25,654.5", 216, 382, 336, 1036],
      ["summary", "Details", 17, 1447, 734, 17],
    ],
  },
  "demo-graphite": {
    article: [672, 1529],
    outline: ["div::generated — check ", "div:audit-receipt:Include / exclude ", "p:answer-text:Total outbound rev", "div:rows-block:#metricrevenue_myr", "div:answer-chart:Total outbound rev", "details:answer-details:Detailsoutbound tr"],
    focus: [
      ["button", "generated — check sources", 17, 17, 196, 25],
      ["button", "⟨sql⟩", 394, 18, 35, 24],
      ["button", "Download CSV", 437, 18, 113, 24],
      ["button", "Download Excel", 17, 50, 121, 24],
      ["button", "Power BI", 146, 50, 67, 24],
      ["button", "Superset", 221, 50, 68, 24],
      ["button", "Share answer", 296, 50, 103, 24],
      ["button", "Check accuracy", 408, 50, 122, 24],
      ["a", "Trust runs", 538, 54, 57, 16],
      ["button", "Copilot prompts", 17, 82, 121, 24],
      ["button", "Show me why", 146, 82, 106, 24],
      ["a", "⟨audit⟩", 259, 86, 37, 16],
      ["button", "RM 25,654.50", 248, 243, 111, 29],
      ["g", "total_revenue: 25,654.5", 168, 431, 336, 1036],
      ["summary", "Details", 17, 1496, 638, 17],
    ],
  },
  "abstain-cream": {
    article: [768, 537],
    outline: ["div::no answer — on pur", "div:abstain-reason:No credentials wer", "div:audit-receipt:Include / exclude ", "p:answer-text:I cannot certify a", "div::Try one of these i", "details:answer-details:DetailsGEN-01: ins"],
    focus: [
      ["button", "no answer — on purpose", 17, 17, 170, 24],
      ["button", "⟨sql⟩", 309, 17, 33, 24],
      ["button", "Download Excel", 350, 17, 114, 24],
      ["button", "Power BI", 472, 17, 63, 24],
      ["button", "Superset", 542, 17, 65, 24],
      ["button", "Share answer", 615, 17, 96, 24],
      ["button", "Check accuracy", 17, 49, 114, 24],
      ["a", "Trust runs", 139, 53, 53, 16],
      ["button", "⟨sources⟩", 200, 49, 68, 24],
      ["a", "⟨audit⟩", 277, 53, 37, 16],
      ["button", "What is our total spend by suppl", 30, 370, 338, 38],
      ["button", "What is total stock value by cat", 374, 370, 295, 38],
      ["button", "Top 5 selling SKUs by revenue", 30, 414, 236, 38],
      ["button", "How many SKUs do we have in inve", 272, 414, 322, 38],
      ["a", "Pick a table in Library", 30, 460, 113, 16],
      ["a", "Ask about this table", 155, 460, 106, 16],
      ["summary", "Details", 17, 503, 734, 17],
    ],
  },
  "abstain-graphite": {
    article: [672, 582],
    outline: ["div::no answer — on pur", "div:abstain-reason:No credentials wer", "div:audit-receipt:Include / exclude ", "p:answer-text:I cannot certify a", "div::Try one of these i", "details:answer-details:DetailsGEN-01: ins"],
    focus: [
      ["button", "no answer — on purpose", 17, 17, 178, 25],
      ["button", "⟨sql⟩", 323, 18, 35, 24],
      ["button", "Download Excel", 365, 18, 121, 24],
      ["button", "Power BI", 494, 18, 67, 24],
      ["button", "Superset", 569, 18, 68, 24],
      ["button", "Share answer", 17, 50, 103, 24],
      ["button", "Check accuracy", 128, 50, 122, 24],
      ["a", "Trust runs", 259, 54, 57, 16],
      ["button", "⟨sources⟩", 324, 50, 73, 24],
      ["a", "⟨audit⟩", 405, 54, 37, 16],
      ["button", "What is our total spend by suppl", 30, 371, 355, 38],
      ["button", "What is total stock value by cat", 30, 415, 309, 38],
      ["button", "Top 5 selling SKUs by revenue", 345, 415, 252, 38],
      ["button", "How many SKUs do we have in inve", 30, 459, 340, 38],
      ["a", "Browse what can be asked", 30, 505, 152, 16],
      ["a", "Attach the missing source", 194, 505, 147, 16],
      ["summary", "Details", 17, 548, 638, 17],
    ],
  },
};

async function open(
  page: Page,
  kind: Kind,
  mode: Mode,
  envelope: Record<string, unknown> = ENVELOPES[kind],
) {
  await page.route("**/v1/chat/ask", (route) => route.fulfill({ json: envelope }));
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  if (mode === "graphite") {
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.getByRole("button", { name: "Switch to operator mode" })).toHaveCount(0);
  }
  await page.getByPlaceholder(/Ask about your data/).fill("What was total revenue?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const answer = page.getByTestId("answer-message").last();
  await expect(answer).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState("networkidle");
  return answer;
}

const FOCUSABLE =
  'a[href],button,summary,input,select,textarea,[tabindex]:not([tabindex="-1"]),h1,h2,h3,h4,h5,h6';

type Item = { tag: string; name: string; x: number; y: number; w: number; h: number };

/** Every visible focusable control and heading in the answer, in DOM order, boxes relative to the card. */
async function controls(answer: Locator): Promise<Item[]> {
  return answer.evaluate((root, sel) => {
    const a = root.getBoundingClientRect();
    const out: Item[] = [];
    for (const el of root.querySelectorAll(sel)) {
      if (!el.checkVisibility({ visibilityProperty: true, contentVisibilityAuto: true })) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const name = (el.getAttribute("aria-label") || el.textContent || "")
        .trim()
        .replace(/\s+/g, " ")
        .slice(0, 32);
      out.push({
        tag: el.tagName.toLowerCase(),
        name,
        x: Math.round(r.left - a.left),
        y: Math.round(r.top - a.top),
        w: Math.round(r.width),
        h: Math.round(r.height),
      });
    }
    return out;
  }, FOCUSABLE);
}

/**
 * The first control that the DOM lists after another one but that is drawn before it:
 * wholly above it, or in the same row and wholly to its left. Null when the two orders agree.
 */
function firstInversion(items: Item[]): string | null {
  for (let i = 1; i < items.length; i++) {
    const a = items[i - 1];
    const b = items[i];
    const above = b.y + b.h <= a.y + 1;
    const sameRow = b.y < a.y + a.h - 1 && a.y < b.y + b.h - 1;
    const leftOf = sameRow && b.x + b.w <= a.x + 1;
    if (above || leftOf) {
      return `"${b.name}" (x${b.x} y${b.y}) follows "${a.name}" (x${a.x} y${a.y}) in the DOM but is drawn ${above ? "above" : "left of"} it`;
    }
  }
  return null;
}

async function tools(answer: Locator): Promise<string[]> {
  const kids = answer.getByTestId("answer-actions").locator("> *");
  return (await kids.allInnerTexts()).map((t) => t.trim());
}

/** Open everything a tool can open, so every panel that used to be `order-last` is on screen. */
async function openEverything(page: Page, answer: Locator) {
  await page.route("**/v1/chat/export.xlsx", (route) =>
    route.fulfill({ status: 500, body: "export unavailable" }),
  );
  await page.route("**/v1/chat/export.bi", (route) =>
    route.fulfill({
      json: {
        ok: true,
        complete: false,
        live_connector: false,
        answer_id: "ans_demo_revenue",
        badge: "L2_VALIDATED",
        abstained: false,
        row_count: 1,
        columns: ["metric", "revenue_myr"],
        source_table: "ask_envelope",
        table: [{ metric: "total_revenue", revenue_myr: 25654.5 }],
        targets: {
          powerbi: {
            status: "stub",
            live_connector: false,
            blocked: [],
            needs_you: ["Desktop or ODBC"],
            filename: "dms_answer.pq",
            power_query_m: "let Source = 1 in Source",
          },
        },
      },
    }),
  );
  await page.route("**/v1/chat/drillthrough", (route) =>
    route.fulfill({ json: { rows: [{ sku: "SKU-A", qty: 3 }] } }),
  );
  const strip = answer.getByTestId("answer-actions");
  await answer.getByRole("button", { name: /generated — check sources/ }).click();
  await strip.getByText("⟨sql⟩", { exact: true }).click();
  await strip.getByText("Copilot prompts", { exact: true }).click();
  await strip.getByText("Check accuracy", { exact: true }).click();
  await strip.getByText("Download Excel", { exact: true }).click();
  await expect(answer.getByText(/Export: export unavailable/)).toBeVisible();
  await strip.getByText("Power BI", { exact: true }).click();
  await expect(answer.getByText(/envelope stub/)).toBeVisible();
  await strip.getByText("Show me why", { exact: true }).click();
  await expect(answer.getByText("Drill-through rows")).toBeVisible();
}

async function noPageOverflow(page: Page) {
  const over = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(over).toBeLessThanOrEqual(0);
}

for (const size of [PHONE, DESKTOP]) {
  const phone = size.width < 1024;
  for (const mode of MODES) {
    for (const kind of ["demo", "abstain"] as const) {
      test.describe(`answer order ${size.width}x${size.height} ${mode} ${kind}`, () => {
        test.use({ viewport: size, hasTouch: phone, isMobile: phone });

        test("DOM order of controls and headings equals the drawn order", async ({ page }) => {
          const answer = await open(page, kind, mode);
          const items = await controls(answer);
          expect(items.length).toBeGreaterThanOrEqual(kind === "demo" ? 12 : phone ? 8 : 15);
          expect(firstInversion(items)).toBeNull();
        });

        if (phone) {
          test("no element in the answer is reordered with CSS order", async ({ page }) => {
            const answer = await open(page, kind, mode);
            const ordered = () =>
              answer.evaluate((root) =>
                [root, ...root.querySelectorAll("*")]
                  .filter((el) => getComputedStyle(el).order !== "0")
                  .map((el) => `${el.tagName.toLowerCase()}[${el.getAttribute("data-testid") ?? ""}] order ${getComputedStyle(el).order}`),
              );
            expect(await ordered()).toEqual([]);
          });
        }
      });
    }
  }
}

for (const mode of MODES) {
  test.describe(`answer order with every panel open 390x844 ${mode}`, () => {
    test.use({ viewport: PHONE, hasTouch: true, isMobile: true });

    test("panels a tool opens sit after it, in the DOM and on screen, with no CSS order", async ({
      page,
    }) => {
      const answer = await open(page, "demo", mode);
      await openEverything(page, answer);
      const items = await controls(answer);
      const names = items.map((i) => i.name);
      const layerLink = mode === "cream" ? "Open the tables" : "See the governed metrics";
      for (const n of ["Copy", "Copy Power Query", "Open Trust", layerLink]) {
        expect(names, `${n} is on screen`).toContain(n);
      }
      expect(firstInversion(items)).toBeNull();
      const ordered = await answer.evaluate((root) =>
        [root, ...root.querySelectorAll("*")]
          .filter((el) => getComputedStyle(el).order !== "0")
          .map((el) => el.tagName.toLowerCase()),
      );
      expect(ordered).toEqual([]);
    });
  });
}

test.describe("answer order with every panel open 1280x800", () => {
  test.use({ viewport: DESKTOP });

  test("desktop order still holds with the panels open", async ({ page }) => {
    const answer = await open(page, "demo", "cream");
    await openEverything(page, answer);
    expect(firstInversion(await controls(answer))).toBeNull();
  });
});

for (const size of [PHONE, SE]) {
  test.describe(`abstained answer tools ${size.width}x${size.height}`, () => {
    test.use({ viewport: size, hasTouch: true, isMobile: true });

    for (const mode of MODES) {
      test(`${mode}: only the tools that can act on an answer with no rows and no SQL`, async ({
        page,
      }) => {
        const answer = await open(page, "abstain", mode);
        expect(await tools(answer)).toEqual(PHONE_ABSTAIN_TOOLS);
        // The explanation stays: reason, the answer text, the receipt, the way out.
        await expect(answer.getByTestId("abstain-reason")).toBeVisible();
        await expect(answer.getByTestId("answer-text")).toBeVisible();
        await expect(answer.getByTestId("audit-receipt")).toBeVisible();
        await expect(answer.getByText("Try one of these instead")).toBeVisible();
        await expect(answer.getByRole("button", { name: /no answer — on purpose/ })).toBeVisible();
        await expect(answer.getByText("Details")).toBeVisible();
      });
    }

    test("what remains does something", async ({ page }) => {
      const answer = await open(page, "abstain", "cream");
      const strip = answer.getByTestId("answer-actions");
      await strip.getByText("Share answer", { exact: true }).click();
      await expect(strip.getByText(/Envelope copied|Copy failed/)).toBeVisible();
      await expect(strip.getByText("⟨audit⟩", { exact: true })).toHaveAttribute("href", /\/(audit|trust)/);
      await strip.getByText("Trust runs", { exact: true }).click();
      await expect(page).toHaveURL(/\/trust/);
    });

    test("an abstained answer that does carry SQL or sources keeps those tools and its Yes/No card", async ({
      page,
    }) => {
      const answer = await open(page, "abstain", "cream", {
        ...ABSTAIN,
        sql_used: "SELECT 1",
        contributing_sources: [
          {
            ref_id: "ref_txn",
            container: "transactions",
            kind: "sql",
            row_count: 10,
            contribution: 1,
            origin_uri: "duckdb://dms_demo/transactions",
            space_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
            extracted_at: null,
            source_kind: null,
          },
        ],
        reconfirm: {
          reason_code: "unknown_measure",
          reason_text: "No measure matched.",
          suggested_question: "Total stock value by category?",
          token: "rcfexample",
        },
      });
      expect(await tools(answer)).toEqual(["⟨sql⟩", "Share answer", "Trust runs", "⟨sources⟩", "⟨audit⟩"]);
      await expect(answer.getByTestId("chat-reconfirm-yes")).toBeVisible();
      await expect(answer.getByTestId("chat-reconfirm-no")).toBeVisible();
      expect(firstInversion(await controls(answer))).toBeNull();
    });
  });
}

test.describe("full tool sets", () => {
  test("demo answer keeps all its tools on a phone", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: PHONE, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const answer = await open(page, "demo", "cream");
    expect(await tools(answer)).toEqual(DEMO_TOOLS);
    await ctx.close();
  });

  test("desktop keeps every tool on an abstained answer", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: DESKTOP });
    const page = await ctx.newPage();
    const answer = await open(page, "abstain", "cream");
    expect(await tools(answer)).toEqual(ALL_ABSTAIN_TOOLS);
    await ctx.close();
  });
});

type Strip = { overflow: boolean; more: boolean; mask: string; atEnd: boolean };

async function strip(answer: Locator): Promise<Strip> {
  return answer.getByTestId("answer-actions").evaluate((el) => {
    const cs = getComputedStyle(el);
    const mask = cs.maskImage || cs.webkitMaskImage || "none";
    return {
      overflow: el.scrollWidth > el.clientWidth + 1,
      more: el.getAttribute("data-more") === "true",
      mask,
      atEnd: el.scrollWidth - el.clientWidth - el.scrollLeft <= 1,
    };
  });
}

for (const size of [PHONE, SE]) {
  for (const mode of MODES) {
    test.describe(`tools strip cue ${size.width}x${size.height} ${mode}`, () => {
      test.use({ viewport: size, hasTouch: true, isMobile: true });

      test("a fade says the strip scrolls, and goes at the end", async ({ page }) => {
        const answer = await open(page, "demo", mode);
        const el = answer.getByTestId("answer-actions");
        await el.scrollIntoViewIfNeeded();
        // The cue follows the scroll through React state, so poll for it.
        await expect.poll(async () => (await strip(answer)).more, "cue at the start").toBe(true);
        const start = await strip(answer);
        expect(start.overflow).toBe(true);
        expect(start.mask, "the fade is drawn").not.toBe("none");

        await el.evaluate((n) => {
          n.scrollLeft = n.scrollWidth / 3;
        });
        await expect.poll(async () => (await strip(answer)).atEnd).toBe(false);
        await expect.poll(async () => (await strip(answer)).more, "cue while more is to the right").toBe(true);
        expect((await strip(answer)).mask).not.toBe("none");

        await el.evaluate((n) => {
          n.scrollLeft = n.scrollWidth;
        });
        await expect.poll(async () => (await strip(answer)).atEnd).toBe(true);
        await expect.poll(async () => (await strip(answer)).more, "no cue at the end").toBe(false);
        expect((await strip(answer)).mask).toBe("none");
        await noPageOverflow(page);
      });

      test("the cue hides no tool and takes no tap; every tool keeps a 44px hit area and 12px text", async ({
        page,
      }) => {
        const answer = await open(page, "demo", mode);
        const el = answer.getByTestId("answer-actions");
        await el.scrollIntoViewIfNeeded();
        const hit = await el.evaluate((n) => {
          const s = n.getBoundingClientRect();
          const out: { name: string; w: number; h: number; font: number; hit: boolean }[] = [];
          for (const k of n.children) {
            // Bring the chip into the strip, then tap its centre.
            k.scrollIntoView({ inline: "nearest", block: "nearest" });
            const r = k.getBoundingClientRect();
            const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            // The smallest font of any text in the chip.
            let font = 99;
            const walker = document.createTreeWalker(k, NodeFilter.SHOW_TEXT);
            for (let t = walker.nextNode(); t; t = walker.nextNode()) {
              if ((t.textContent ?? "").trim() && t.parentElement)
                font = Math.min(font, parseFloat(getComputedStyle(t.parentElement).fontSize));
            }
            out.push({
              name: (k.textContent ?? "").trim(),
              w: Math.round(r.width),
              h: Math.round(r.height),
              font,
              hit: (top === k || k.contains(top)) && r.left >= s.left - 0.5 && r.right <= s.right + 0.5,
            });
          }
          return out;
        });
        expect(hit.length).toBe(DEMO_TOOLS.length);
        for (const t of hit) {
          expect(t.h, `${t.name} height`).toBeGreaterThanOrEqual(TARGET);
          expect(t.w, `${t.name} width`).toBeGreaterThanOrEqual(TARGET);
          expect(t.font, `${t.name} text size`).toBeGreaterThanOrEqual(12);
          expect(t.hit, `${t.name} is what a tap lands on`).toBe(true);
        }
        // At the start, a tap in the faded right edge lands in the strip, not on an overlay.
        await el.evaluate((n) => {
          n.scrollLeft = 0;
        });
        const edge = await el.evaluate((n) => {
          const s = n.getBoundingClientRect();
          const top = document.elementFromPoint(s.right - 6, s.top + s.height / 2);
          return !!top && n.contains(top);
        });
        expect(edge, "the right edge of the strip takes the tap").toBe(true);
        // The last chip is reachable: scrolled to the end it sits wholly inside the strip.
        await el.evaluate((n) => {
          n.scrollLeft = n.scrollWidth;
        });
        await expect.poll(async () => (await strip(answer)).atEnd).toBe(true);
        const last = await el.evaluate((n) => {
          const s = n.getBoundingClientRect();
          const r = n.lastElementChild!.getBoundingClientRect();
          return { right: r.right, stripRight: s.right, left: r.left, stripLeft: s.left };
        });
        expect(last.right).toBeLessThanOrEqual(last.stripRight + 0.5);
        expect(last.left).toBeGreaterThanOrEqual(last.stripLeft);
        await noPageOverflow(page);
      });

      test("a strip that fits shows no cue", async ({ page }) => {
        const answer = await open(page, "abstain", mode);
        const s = await strip(answer);
        expect(s.overflow).toBe(false);
        expect(s.more).toBe(false);
        expect(s.mask).toBe("none");
      });
    });
  }
}

for (const mode of MODES) {
  test.describe(`desktop has no strip cue 1280x800 ${mode}`, () => {
    test.use({ viewport: DESKTOP });

    test("no mask on the tools at lg", async ({ page }) => {
      const answer = await open(page, "demo", mode);
      const s = await strip(answer);
      expect(s.more).toBe(false);
      expect(s.mask).toBe("none");
    });
  });
}

for (const mode of MODES) {
  for (const kind of ["demo", "abstain"] as const) {
    test.describe(`desktop unchanged 1280x800 ${mode} ${kind}`, () => {
      test.use({ viewport: DESKTOP });

      test("controls keep their DOM order and boxes from before the phone fix", async ({ page }) => {
        const answer = await open(page, kind, mode);
        const ref = REF_1280[`${kind}-${mode}`];
        const live = await answer.evaluate((root, sel) => {
          const a = root.getBoundingClientRect();
          const outline = [...root.children].map(
            (c) =>
              `${c.tagName.toLowerCase()}:${c.getAttribute("data-testid") ?? ""}:${(c.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 18)}`,
          );
          const focus: [string, string, number, number, number, number][] = [];
          for (const el of root.querySelectorAll(sel)) {
            if (!el.checkVisibility({ visibilityProperty: true, contentVisibilityAuto: true })) continue;
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            const name = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 32);
            focus.push([el.tagName.toLowerCase(), name, Math.round(r.left - a.left), Math.round(r.top - a.top), Math.round(r.width), Math.round(r.height)]);
          }
          return { article: [Math.round(a.width), Math.round(a.height)] as [number, number], outline, focus };
        }, FOCUSABLE);
        expect(live).toEqual(ref);
      });
    });
  }
}
