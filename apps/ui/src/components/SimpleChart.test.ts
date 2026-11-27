import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SimpleChart } from "./SimpleChart";
import type { ModelChartSpec } from "./studio/chartSpec";

function html(chart: ModelChartSpec & { value?: number }, rows: Record<string, unknown>[]): string {
  return renderToStaticMarkup(createElement(SimpleChart, { chart, rows }));
}

describe("SimpleChart kinds", () => {
  const rows = [
    { country: "MY", spend: 10, qty: 4 },
    { country: "SG", spend: 6, qty: 2 },
  ];

  it("bar and line stay free of the new shape marker", () => {
    const bar = renderToStaticMarkup(
      createElement(SimpleChart, {
        chart: { kind: "bar", x: "country", y: "spend", title: "Result" },
        rows,
      }),
    );
    expect(bar).toContain("<rect");
    expect(bar).not.toContain("data-chart-shape");
    const line = renderToStaticMarkup(
      createElement(SimpleChart, {
        chart: { kind: "line", x: "country", y: "spend", title: "Result" },
        rows,
      }),
    );
    expect(line).toContain("<polyline");
    expect(line).not.toContain("data-chart-shape");
  });

  it("draws a pie from cell values only", () => {
    const markup = html(
      { kind: "pie", x: "country", y: ["spend"], series: null, value: 99999 },
      rows,
    );
    expect(markup).toContain('data-chart-shape="pie"');
    expect(markup).toContain('data-cell="10"');
    expect(markup).toContain('data-cell="6"');
    expect(markup).toContain("MY");
    expect(markup).not.toContain("99999");
  });

  it("draws a stacked chart from cell values only", () => {
    const markup = html(
      { kind: "stacked", x: "country", y: ["spend", "qty"], series: null, value: 99999 },
      rows,
    );
    expect(markup).toContain('data-chart-shape="stacked"');
    expect(markup).toContain('data-cell="10"');
    expect(markup).toContain('data-cell="4"');
    expect(markup).toContain('data-series="spend"');
    expect(markup).toContain('data-series="qty"');
    expect(markup).not.toContain("99999");
  });

  it("draws a multi-line chart from cell values only", () => {
    const markup = html(
      { kind: "multi_line", x: "country", y: ["spend", "qty"], series: null },
      rows,
    );
    expect(markup).toContain('data-chart-shape="multi_line"');
    const lines = markup.match(/<polyline/g) ?? [];
    expect(lines.length).toBe(2);
    expect(markup).toContain('data-cell="10"');
    expect(markup).toContain('data-cell="4"');
    expect(markup).not.toContain("99999");
  });

  it("draws a scatter from cell pairs and skips a non-numeric cell", () => {
    const markup = html({ kind: "scatter", x: "spend", y: ["qty"], series: null }, [
      { spend: 1, qty: 2 },
      { spend: 3, qty: "nope" },
    ]);
    expect(markup).toContain('data-chart-shape="scatter"');
    expect(markup).toContain('data-x="1"');
    expect(markup).toContain('data-y="2"');
    expect(markup.match(/<circle/g)?.length).toBe(1);
    expect(markup).not.toContain('data-y="0"');
    expect(markup).not.toContain("nope");
  });
});
