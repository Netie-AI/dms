import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PromoteReceiptPanel } from "./PromoteReceiptPanel";
import type { PromoteReceiptState } from "../lib/api";

const recorded: PromoteReceiptState = {
  target: "silver.sales",
  state: "recorded",
  recorded_at: "2026-09-03T06:00:00Z",
  runs: 1,
  receipt: {
    run_id: "run_1",
    target: "silver.sales",
    sources: ["bronze.sales_raw"],
    source_rows: 1000,
    passed: 997,
    quarantined: 3,
    unmatched: 0,
    reconciled: true,
    counts_by_reason: { below_min: 2, null_rate: 1 },
    dedup_key: ["invoice_no"],
    lineage: "propagate",
    table: "silver.sales",
    quarantine_table: "quarantine.silver_sales",
  },
};

describe("PromoteReceiptPanel", () => {
  it("renders the three receipt numbers as received, reasons by count desc", () => {
    const html = renderToStaticMarkup(createElement(PromoteReceiptPanel, { state: recorded }));
    expect(html).toContain("data-testid=\"promote-source-rows\">1000");
    expect(html).toContain("data-testid=\"promote-passed\">997");
    expect(html).toContain("data-testid=\"promote-quarantined\">3");
    expect(html).toContain("1000");
    expect(html).toContain("997");
    expect(html).toContain("3");
    const reasons = html.split("data-testid=\"promote-reasons\"")[1] ?? "";
    expect(reasons.indexOf("below_min: 2")).toBeLessThan(reasons.indexOf("null_rate: 1"));
  });

  it("has a no_receipt_yet branch for ticket 4 to style", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: { target: "silver.sales", state: "no_receipt_yet", runs: 0, receipt: null },
      }),
    );
    expect(html).toContain("promote-no-receipt");
    expect(html).toContain("TODO(EPIC-024 T4)");
  });

  it("has an unhealthy recorded branch for ticket 4 to style", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: {
          ...recorded,
          receipt: { ...recorded.receipt, reconciled: false, unmatched: -4 },
        },
      }),
    );
    expect(html).toContain("promote-unhealthy");
    expect(html).toContain("TODO(EPIC-024 T4)");
    expect(html).toContain("data-testid=\"promote-unmatched\">-4");
  });
});
