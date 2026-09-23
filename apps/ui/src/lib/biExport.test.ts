import { describe, expect, it } from "vitest";
import { biCopyText, biPanelHeadline, type BiExportResponse } from "./biExport";

const payload: BiExportResponse = {
  ok: true,
  complete: false,
  live_connector: false,
  answer_id: "ans_export_two",
  badge: "L2_VALIDATED",
  row_count: 2,
  columns: ["sku", "qty"],
  source_table: "rows",
  table: [
    { sku: "SKU-ALPHA", qty: 1 },
    { sku: "SKU-BETA", qty: 2 },
  ],
  targets: {
    powerbi: {
      status: "envelope_stub",
      live_connector: false,
      blocked: ["live_odbc"],
      needs_you: ["Paste power_query_m into Get Data > Blank Query. (NEEDS-YOU)."],
      filename: "dms_answer_ans_export_two.pq",
      power_query_m: 'let\n    Source = #table({"sku"}, {{"SKU-ALPHA"}})\nin\n    Source\n',
    },
    superset: {
      status: "envelope_stub",
      live_connector: false,
      blocked: ["sqlalchemy_uri"],
      needs_you: ["SQLAlchemy URI is not shipped (NEEDS-YOU)."],
      filename: "dms_answer_ans_export_two.superset.json",
      dataset: { sqlalchemy_uri: null, rows: [{ sku: "SKU-ALPHA", qty: 1 }] },
    },
  },
};

describe("biExport", () => {
  it("copies the Power Query M and does not stamp COMPLETE", () => {
    const m = biCopyText(payload.targets.powerbi!);
    expect(m).toContain("SKU-ALPHA");
    expect(m).not.toContain("COMPLETE");
    expect(m).not.toContain("99.95");
    expect(payload.complete).toBe(false);
  });

  it("serializes the Superset dataset without a URI secret", () => {
    const text = biCopyText(payload.targets.superset!);
    expect(text).toContain("SKU-ALPHA");
    expect(text).toContain('"sqlalchemy_uri": null');
    expect(text.toLowerCase()).not.toContain("password");
  });

  it("headlines stay stubs / NEEDS-YOU, not a live connector", () => {
    expect(biPanelHeadline(payload, "powerbi")).toContain("NEEDS-YOU");
    expect(biPanelHeadline(payload, "superset")).toContain("not DMS chrome");
    expect(biPanelHeadline(payload, "powerbi")).not.toContain("COMPLETE");
  });
});
