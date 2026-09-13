import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { AnswerEnvelope } from "./types";
import { sourcesForPanel, sourcesHeadline, tablesFromSql } from "./sourcePanel";

const here = dirname(fileURLToPath(import.meta.url));

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

describe("STUDIO-MOBILE-01 layout wiring", () => {
  it("Chat Sources overlays below lg and keeps the 22rem dock at lg", () => {
    const panel = readFileSync(join(here, "../components/SourcePanel.tsx"), "utf8");
    expect(panel).toMatch(/max-lg:fixed/);
    expect(panel).toMatch(/w-\[22rem\]/);
    expect(panel).toMatch(/max-lg:w-\[min\(22rem,calc\(100%-2\.75rem\)\)\]/);
    expect(panel).toMatch(/data-testid="source-panel-open"/);
    expect(panel).toMatch(/min-h-11/);
    const ctx = readFileSync(join(here, "../context/AppContext.tsx"), "utf8");
    expect(ctx).toMatch(/sourcesStartOpen/);
    expect(ctx).toMatch(/shouldAutoOpenSourcesAfterAsk/);
  });

  it("Studio keeps the desktop two-column grid and hides files/SQLSRC below lg until opened", () => {
    const page = readFileSync(join(here, "../pages/StudioPage.tsx"), "utf8");
    expect(page).toMatch(/lg:grid-cols-\[22rem_1fr\]/);
    expect(page).toMatch(/max-lg:hidden/);
    expect(page).toMatch(/studio-sources-toggle/);
    expect(page).toMatch(/<SqlSourcePanel /);
  });
});
