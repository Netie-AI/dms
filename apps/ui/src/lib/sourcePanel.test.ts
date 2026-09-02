import { describe, expect, it } from "vitest";
import type { AnswerEnvelope } from "./types";
import { sourcesForPanel, sourcesHeadline, tablesFromSql } from "./sourcePanel";

const base: AnswerEnvelope = {
  answer_id: "ans_1",
  text: "ok",
  values: [{ id: "v1", value: 1, label: "n" }],
  badge: "L0_CERTIFIED",
  assumptions: [],
  as_of: "2026-09-03T00:00:00Z",
  contributing_sources: [],
  rows: [{ country: "MY", total_spend_myr: 1 }],
};

describe("sourcePanel", () => {
  it("keeps Cortex-attached sources", () => {
    const src = {
      ref_id: "s1",
      container: "suppliers.xlsx",
      kind: "xlsx" as const,
      row_count: 91,
      contribution: 1,
    };
    const got = sourcesForPanel({ ...base, contributing_sources: [src] });
    expect(got).toEqual([src]);
  });

  it("falls back to grounded_tables then SQL FROM", () => {
    const grounded = sourcesForPanel({
      ...base,
      grounded_tables: ["bronze.encoding_value_norm_Sales"],
    });
    expect(grounded[0]?.container).toBe("bronze.encoding_value_norm_Sales");
    expect(grounded[0]?.kind).toBe("xlsx");

    const fromSql = sourcesForPanel({
      ...base,
      sql_used: "SELECT country, SUM(spend) FROM suppliers GROUP BY 1",
    });
    expect(fromSql[0]?.container).toBe("suppliers");
    expect(fromSql[0]?.kind).toBe("sql");
  });

  it("does not say no-answer when an envelope exists without cards", () => {
    expect(sourcesHeadline(null, [], 0)).toContain("No answer yet");
    expect(sourcesHeadline(base, [], 0)).toContain("no file card");
    expect(sourcesHeadline(base, sourcesForPanel({ ...base, sql_used: "SELECT 1 FROM inventory" }), 8)).toMatch(
      /1 table/,
    );
  });

  it("parses quoted bronze FROM", () => {
    expect(tablesFromSql('SELECT * FROM bronze."blank_rows_hanging_Sales"')).toEqual([
      "bronze.blank_rows_hanging_Sales",
    ]);
  });
});
