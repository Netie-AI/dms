import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ResultView, type StudioAskEnvelope } from "./ResultView";
import { STAMP_SLOTS } from "./stamps";

const here = dirname(fileURLToPath(import.meta.url));

function html(envelope: StudioAskEnvelope | null): string {
  return renderToStaticMarkup(createElement(ResultView, { envelope }));
}

function stamp(markup: string, id: string): string {
  const m = markup.match(new RegExp(`data-stamp="${id}"[^>]*>([^<]*)<`));
  return m?.[1] ?? "";
}

function answered(over: Partial<StudioAskEnvelope> = {}): StudioAskEnvelope {
  return {
    badge: "L2_VALIDATED",
    abstained: false,
    text: "Spend is higher in MY.\n\nInsights:\n- Check the KL lane",
    sql_used: "SELECT country, spend FROM sales",
    assumptions: ["live Cortex ask"],
    rows: [
      { country: "MY", spend: 10 },
      { country: "SG", spend: 4 },
    ],
    ...over,
  };
}

describe("Studio result view", () => {
  it("answered category and numeric renders a bar chart, SQL, rows, and insight", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({
      country: `C${i}`,
      spend: i + 1,
    }));
    const markup = html(
      answered({
        rows,
        served_provider: "groq",
        served_model: "openai/gpt-oss-120b",
      }),
    );
    expect(markup).toContain('data-testid="studio-sql"');
    expect(markup).toContain("SELECT country, spend FROM sales");
    expect(markup).toContain('data-testid="studio-sql-copy"');
    expect(markup).toContain(">Copy<");
    expect(markup).toContain("font-mono");
    expect(markup).not.toContain("<textarea");
    expect(markup).toContain('data-testid="studio-insight"');
    expect(markup).toContain("Spend is higher in MY.");
    expect(markup).toContain("Check the KL lane");
    expect(markup).toContain('data-testid="studio-chart"');
    expect(markup).toContain('data-chart-kind="bar"');
    expect(markup).toContain("<svg");
    expect(markup).toContain('data-testid="studio-rows"');
    expect(markup).toContain("Showing 1-10 of 12 rows");
    expect(markup).toContain(">Next<");
    expect(markup).not.toContain("<select");
    expect(stamp(markup, "served_provider")).toBe("groq");
    expect(stamp(markup, "served_model")).toBe("openai/gpt-oss-120b");
    expect(stamp(markup, "cortex-pin")).toBe("not stamped");
    expect(stamp(markup, "contract-version")).toBe("not stamped");
    expect(stamp(markup, "timings")).toBe("not stamped");
  });

  it("answered date and numeric renders a line chart", () => {
    const markup = html(
      answered({
        text: "Quantity by day.",
        sql_used: "SELECT day, qty FROM movements",
        rows: [
          { day: "2026-01-01", qty: 1 },
          { day: "2026-01-02", qty: 3 },
        ],
      }),
    );
    expect(markup).toContain('data-chart-kind="line"');
    expect(markup).toContain("<svg");
    expect(markup).not.toContain('data-testid="studio-no-chart"');
  });

  it("answered rows with no chartable shape say so", () => {
    const markup = html(
      answered({
        text: "Two measures, no category.",
        sql_used: "SELECT a, b FROM dual",
        rows: [{ a: 1, b: 2 }],
      }),
    );
    expect(markup).toContain('data-testid="studio-no-chart"');
    expect(markup).toContain("no chartable shape");
    expect(markup).not.toContain('data-testid="studio-chart"');
    expect(markup).toContain('data-testid="studio-rows"');
    expect(markup).toContain('data-testid="studio-insight"');
    expect(markup).not.toContain('data-testid="studio-abstain"');
  });

  it("abstain shows the named reason and no rows", () => {
    const markup = html({
      badge: "ABSTAIN",
      abstained: true,
      abstain_reason: "reserved_param:as_of",
      text: "A longer sentence that is not the stamp.",
      assumptions: ["GEN-01: validate:ungranted:alerts", "live Cortex ask"],
      sql_used: null,
      rows: [{ sku: "FAKE_ROW_SHOULD_NOT_RENDER", spend: 999 }],
    });
    expect(markup).toContain('data-testid="studio-abstain"');
    expect(markup).toContain('data-testid="studio-abstain-reason"');
    expect(markup).toContain("reserved_param:as_of");
    expect(markup).not.toContain("FAKE_ROW_SHOULD_NOT_RENDER");
    expect(markup).not.toContain('data-testid="studio-rows"');
    expect(markup).not.toContain('data-testid="studio-chart"');
    expect(markup).not.toContain('role="alert"');
    expect(markup).not.toContain('data-testid="studio-insight"');
    expect(markup).toContain("no SQL");
  });

  it("missing stamps read not stamped", () => {
    const markup = html(answered());
    for (const slot of STAMP_SLOTS) {
      expect(stamp(markup, slot.id)).toBe("not stamped");
    }
    expect(markup).toContain("Cortex pin");
    expect(markup).toContain("Contract version");
    expect(markup).toContain("Timings");
  });

  it("StudioPage mounts ResultView", () => {
    const studio = readFileSync(join(here, "../../pages/StudioPage.tsx"), "utf8");
    expect(studio).toContain("ResultView");
    expect(studio).toContain("studioEnvelope");
  });
});
