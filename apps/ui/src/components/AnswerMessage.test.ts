import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AnswerMessage, layerCopy } from "@/components/AnswerMessage";
import { AppProvider } from "@/context/AppContext";
import type { AnswerEnvelope } from "@/lib/types";

function markup(envelope: AnswerEnvelope): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(AppProvider, null, createElement(AnswerMessage, { envelope })),
    ),
  );
}

function envelope(reconfirm: AnswerEnvelope["reconfirm"]): AnswerEnvelope {
  return {
    answer_id: "ans_reconfirm",
    text: "The query needs a definition.",
    values: [],
    badge: "ABSTAIN",
    abstained: true,
    assumptions: [],
    as_of: "2026-10-28T00:00:00Z",
    contributing_sources: [],
    reconfirm,
  };
}

describe("Chat reconfirm card", () => {
  it("shows Yes without opening Details when a definition is present", () => {
    const html = markup(
      envelope({
        reason_code: "undeclared_join",
        reason_text: "That join is not in the approved ontology.",
        suggested_question: "",
        definition: "orders.customer_id = customers.id",
        token: "rcfexample",
      }),
    );
    const yesAt = html.indexOf('data-testid="chat-reconfirm-yes"');
    const detailsAt = html.indexOf("<details");
    expect(yesAt).toBeGreaterThan(-1);
    expect(detailsAt).toBeGreaterThan(yesAt);
    expect(html).toContain('data-testid="chat-reconfirm-no"');
    expect(html).toContain(">Yes<");
    expect(html).toContain(">No<");
    expect(html).toContain("That join is not in the approved ontology.");
    expect(html).toContain("orders.customer_id = customers.id");
    expect(html).not.toMatch(/<details[^>]*\sopen[\s>]/);
  });

  it("shows Yes for a suggestion when there is no definition", () => {
    const html = markup(
      envelope({
        reason_code: "unknown_measure",
        reason_text: "no measure matched",
        suggested_question: "closest stock question",
        token: "rcfexample",
      }),
    );
    const yesAt = html.indexOf('data-testid="chat-reconfirm-yes"');
    const detailsAt = html.indexOf("<details");
    const studioAt = html.indexOf('data-testid="studio-reconfirm-yes"');
    expect(yesAt).toBeGreaterThan(-1);
    expect(detailsAt).toBeGreaterThan(yesAt);
    expect(studioAt).toBeGreaterThan(detailsAt);
    expect(html).toContain('data-testid="studio-reconfirm-no"');
    expect(html).toContain("closest stock question");
    expect(html).toContain("no measure matched");
  });

  it("stays byte-identical when clarify and access_needed are absent", () => {
    const base = envelope(null);
    const html = markup(base);
    const withNulls = markup({ ...base, clarify: null, access_needed: null });
    const junk = markup({
      ...base,
      clarify: { question: "Which one?" } as AnswerEnvelope["clarify"],
      access_needed: { table: "orders" } as AnswerEnvelope["access_needed"],
    });
    expect(withNulls).toBe(html);
    expect(junk).toBe(html);
    expect(html).not.toContain("clarify-card");
    expect(html).not.toContain("access-card");
    expect(html).not.toContain("Ask the steward for access");
  });

  it("hides the card when the offer has no suggestion and no definition", () => {
    const html = markup(
      envelope({
        reason_code: "unknown_measure",
        reason_text: "no measure matched",
        suggested_question: "   ",
        token: "rcfexample",
      }),
    );
    expect(html).not.toContain('data-testid="chat-reconfirm-yes"');
  });
});

describe("Document answer layer copy", () => {
  const doc: AnswerEnvelope = {
    answer_id: "ans_doc",
    text: "Damaged goods may be returned with the original receipt.",
    values: [{ id: "v_count", value: 0, label: "row_count" }],
    badge: "L2_VALIDATED",
    abstained: false,
    sql_used: "-- document retrieval (no SQL)",
    assumptions: [],
    as_of: "2026-10-30T00:00:00Z",
    contributing_sources: [],
    route: "document",
  };

  it("says the documents answered, not generated SQL", () => {
    expect(layerCopy(doc).headline).toBe("Answered from this Space's documents.");
    expect(layerCopy(doc).headline).not.toMatch(/SQL/);
  });

  it("keeps the badge copy for every other route", () => {
    expect(layerCopy({ ...doc, route: "rag" }).headline).toBe(
      "This SQL was generated, then checked.",
    );
  });
});

const STOCK_ROWS = [
  { category: "ELECTRONICS", stock_value_myr: 69729521.94 },
  { category: "PACKAGING", stock_value_myr: 15015 },
];

const STOCK_DUMP = [
  "Found 2 row(s).",
  "  - category=ELECTRONICS, stock_value_myr=69729521.94",
  "  - category=PACKAGING, stock_value_myr=15015.0",
].join("\n");

function stockEnvelope(ux: boolean): AnswerEnvelope {
  return {
    answer_id: "ans_stock",
    text: STOCK_DUMP,
    values: [
      { id: "v0", value: 69729521.94, label: "stock_value_myr" },
      { id: "v1", value: 15015, label: "stock_value_myr" },
    ],
    badge: "L2_VALIDATED",
    abstained: false,
    sql_used: "SELECT category, stock_value_myr FROM inventory",
    assumptions: [],
    as_of: "2026-11-28T00:00:00Z",
    contributing_sources: [],
    rows: STOCK_ROWS,
    ux_chart: ux ? true : undefined,
  };
}

describe("UX-CHART-01 answer view", () => {
  it("flag off keeps the row listing and the Power BI label", () => {
    const html = markup(stockEnvelope(false));
    expect(html).toContain("category=ELECTRONICS");
    expect(html).toContain("stock_value_myr=69729521.94");
    expect(html).not.toContain('data-testid="ux-chart"');
    expect(html).not.toContain("Export file");
    expect(html).toContain("Power BI");
    expect(html).toContain("Superset");
    expect(html).not.toContain("envelope stub");
  });

  it("does not print the row dump beside the table", () => {
    const html = markup(stockEnvelope(true));
    expect(html).not.toContain("category=");
    expect(html).not.toContain("stock_value_myr=");
    expect(html).toContain('data-testid="ux-summary"');
    expect(html).toContain("ELECTRONICS is highest at");
    expect(html).toContain("RM 69,729,521.94");
    expect(html).toContain("across 2 rows.");
    expect(html).toContain('data-value-id="v0"');
    expect(html).toContain('data-chart-kind="bar"');
    expect(html).toContain('data-point-count="2"');
    expect(html).toContain(">Export file<");
    expect(html).not.toContain(">Power BI<");
    expect(html).not.toContain(">Superset<");
    expect(html).not.toContain("envelope stub");
  });

  it("hides file exports on an abstain with no rows", () => {
    const html = markup({
      ...stockEnvelope(true),
      text: "No.",
      badge: "ABSTAIN",
      abstained: true,
      rows: [],
      values: [],
      sql_used: undefined,
    });
    expect(html).not.toContain("Download CSV");
    expect(html).not.toContain("Download Excel");
    expect(html).not.toContain("Export file");
    expect(html).not.toContain("Power BI");
    expect(html).not.toContain("Superset");
    expect(html).not.toContain('data-testid="ux-chart"');
  });
});
