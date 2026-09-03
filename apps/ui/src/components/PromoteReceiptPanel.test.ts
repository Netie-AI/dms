import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PromoteReceiptPanel } from "./PromoteReceiptPanel";
import type { PromoteReceipt, PromoteReceiptState } from "../lib/api";

function recorded(receipt: PromoteReceipt): PromoteReceiptState {
  return {
    target: receipt.target,
    state: "recorded",
    recorded_at: "2026-09-03T06:00:00Z",
    runs: 1,
    receipt,
  };
}

const healthyReceipt: PromoteReceipt = {
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
};

describe("PromoteReceiptPanel", () => {
  it("healthy: 1000 -> 997 + 3, no refusal token", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, { state: recorded(healthyReceipt) }),
    );
    expect(html).toContain('data-testid="receipt-healthy"');
    expect(html).toContain('data-testid="promote-source-rows">1000');
    expect(html).toContain('data-testid="promote-passed">997');
    expect(html).toContain('data-testid="promote-quarantined">3');
    expect(html).not.toContain("--color-danger");
    expect(html).not.toContain("receipt-defect");
    const reasons = html.split('data-testid="promote-reasons"')[1] ?? "";
    expect(reasons.indexOf("below_min: 2")).toBeLessThan(reasons.indexOf("null_rate: 1"));
  });

  it("lost: unmatched 3", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: recorded({
          ...healthyReceipt,
          passed: 997,
          quarantined: 0,
          unmatched: 3,
          reconciled: false,
        }),
      }),
    );
    expect(html).toContain('data-testid="receipt-defect"');
    expect(html).toContain("--color-danger");
    expect(html).toContain('data-testid="receipt-defect-reason"');
    expect(html).toContain(
      "3 rows did not arrive: they are in neither the target nor quarantine.",
    );
  });

  it("fan-out: unmatched -400", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: recorded({
          ...healthyReceipt,
          passed: 1400,
          quarantined: 0,
          unmatched: -400,
          reconciled: false,
          counts_by_reason: { join_cardinality_change: 400 },
        }),
      }),
    );
    expect(html).toContain("Join fan-out: 400 more rows came out than went in.");
    expect(html).toContain('data-testid="promote-source-rows">1000');
    expect(html).toContain('data-testid="promote-passed">1400');
    expect(html).toContain('data-testid="promote-quarantined">0');
  });

  it("unmeasured: no conservation line", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: recorded({
          ...healthyReceipt,
          source_rows: null,
          passed: 10,
          quarantined: 0,
          unmatched: 0,
          reconciled: false,
        }),
      }),
    );
    expect(html).toContain("Cannot be reconciled: this run recorded no input count.");
    expect(html).not.toContain("promote-conservation");
    expect(html).not.toContain("null -&gt;");
  });

  it("mismatch: difference D from the three receipt numbers", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: recorded({
          ...healthyReceipt,
          passed: 900,
          quarantined: 0,
          unmatched: 0,
          reconciled: false,
        }),
      }),
    );
    expect(html).toContain(
      "Row count does not add up: source_rows and passed + quarantined differ by 100.",
    );
  });

  it("none: muted sentence and no digit", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: { target: "silver.sales", state: "no_receipt_yet", runs: 0, receipt: null },
      }),
    );
    expect(html).toContain('data-testid="receipt-none"');
    expect(html).toContain("No promote has run for this table yet.");
    const text = html.replace(/<[^>]+>/g, "");
    expect(text).not.toMatch(/\d/);
  });

  it("error: refused read is not no_receipt_yet", () => {
    const html = renderToStaticMarkup(
      createElement(PromoteReceiptPanel, {
        state: null,
        error: "Cortex gate is unreachable. Start Cortex before writing -- an ungated change is refused.",
      }),
    );
    expect(html).toContain('data-testid="receipt-error"');
    expect(html).toContain("Cortex gate is unreachable");
    expect(html).toContain("--color-danger");
    expect(html).not.toContain("receipt-none");
  });
});
