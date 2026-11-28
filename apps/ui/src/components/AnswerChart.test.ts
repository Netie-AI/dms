import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerChart } from "./AnswerChart";

function html(rows: Record<string, unknown>[]): string {
  return renderToStaticMarkup(
    createElement(AnswerChart, {
      rows,
      values: [],
      onValue: () => undefined,
    }),
  );
}

describe("AnswerChart", () => {
  it("draws every served row for a category bar", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
      category: `C${i}`,
      stock_value_myr: i + 1,
    }));
    const markup = html(rows);
    expect(markup).toContain('data-testid="ux-chart"');
    expect(markup).toContain('data-chart-kind="bar"');
    expect(markup).toContain('data-point-count="30"');
    expect(markup.match(/<rect/g)?.length).toBe(30);
    expect(markup).toContain('data-testid="ux-chart-kind-bar"');
    expect(markup).toContain('data-testid="ux-chart-kind-line"');
    expect(markup).toContain('data-testid="ux-chart-kind-table"');
    expect(markup).toContain('data-testid="ux-chart-kind-kpi"');
    expect(markup).toContain("Download PNG");
  });

  it("draws a line, a KPI, and a scatter from the row shape", () => {
    const line = html([
      { day: "2024-01-01", qty: 3 },
      { day: "2024-02-01", qty: 5 },
    ]);
    expect(line).toContain('data-chart-kind="line"');
    expect(line).toContain("<polyline");
    const kpi = html([{ stock_value_myr: 10 }]);
    expect(kpi).toContain('data-chart-kind="kpi"');
    expect(kpi).toContain('data-testid="ux-kpi"');
    expect(kpi).toContain("RM 10.00");
    const scatter = html([
      { units: 1, revenue: 4 },
      { units: 3, revenue: 9 },
    ]);
    expect(scatter).toContain('data-chart-kind="scatter"');
    expect(scatter).toContain('data-testid="ux-chart-kind-scatter"');
    expect(scatter).toContain('data-point-count="2"');
  });
});
