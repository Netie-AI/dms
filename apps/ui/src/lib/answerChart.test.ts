import { describe, expect, it } from "vitest";
import {
  activeChart,
  chartRows,
  chartShape,
  connectorButtonLabel,
  kpiValue,
  presentAnswer,
  summaryText,
  type FigureValue,
} from "./answerChart";

const stock = [
  { category: "ELECTRONICS", stock_value_myr: 69729521.94 },
  { category: "PACKAGING", stock_value_myr: 15015 },
];

const values: FigureValue[] = [
  { id: "v0", value: 69729521.94, label: "stock_value_myr" },
  { id: "v1", value: 15015, label: "stock_value_myr" },
];

const dump = [
  "Found 2 row(s).",
  "  - category=ELECTRONICS, stock_value_myr=69729521.94",
  "  - category=PACKAGING, stock_value_myr=15015.0",
].join("\n");

describe("chart shape", () => {
  it("picks a bar for a category and one measure", () => {
    expect(chartShape(stock)?.kind).toBe("bar");
    expect(chartShape(stock)?.x).toBe("category");
    expect(chartShape(stock)?.y).toBe("stock_value_myr");
  });

  it("picks a line for a date and a measure", () => {
    const rows = [
      { day: "2024-01-01", qty: 3 },
      { day: "2024-02-01", qty: 5 },
    ];
    expect(chartShape(rows)?.kind).toBe("line");
    expect(chartShape(rows)?.x).toBe("day");
  });

  it("picks a KPI card for a single value", () => {
    expect(chartShape([{ stock_value_myr: 10 }])?.kind).toBe("kpi");
  });

  it("picks a scatter for two measures", () => {
    const rows = [
      { units: 1, revenue: 4 },
      { units: 2, revenue: 9 },
    ];
    const shape = chartShape(rows);
    expect(shape?.kind).toBe("scatter");
    expect(shape?.x).toBe("units");
    expect(shape?.y).toBe("revenue");
  });

  it("does not drop rows when drawing", () => {
    const view = chartShape(stock);
    expect(view).not.toBeNull();
    const plotted = chartRows(stock, view!);
    expect(plotted.rows).toBe(stock);
    expect(plotted.rows).toHaveLength(2);
  });

  it("a KPI switch reads the first cell, not a sum", () => {
    expect(activeChart(stock, "kpi").kind).toBe("kpi");
    expect(kpiValue(stock, "stock_value_myr")).toBe(69729521.94);
    expect(activeChart(stock, "table").kind).toBe("table");
  });
});

describe("summary", () => {
  it("replaces the key=value listing with one sentence", () => {
    const presented = presentAnswer(dump, stock, values);
    expect(presented.mode).toBe("summary");
    if (presented.mode !== "summary") return;
    const text = summaryText(presented.parts);
    expect(presented.lead).toBe("");
    expect(text).not.toContain("category=");
    expect(text).not.toContain("stock_value_myr=");
    expect(text).toContain("ELECTRONICS is highest at RM 69,729,521.94");
    expect(text).toContain("across 2 rows.");
    expect(text).toContain(", total ");
    const figure = presented.parts.find((part) => part.kind === "figure");
    expect(figure && figure.kind === "figure" ? figure.id : "").toBe("v0");
  });

  it("leaves a sentence that is not a listing", () => {
    const text = "Home revenue is 1,199,018.49 MYR.";
    expect(presentAnswer(text, stock, values)).toEqual({ mode: "keep", text });
  });
});

describe("export label", () => {
  it("says Export file until a live connector exists", () => {
    expect(connectorButtonLabel(false, "Power BI")).toBe("Export file");
    expect(connectorButtonLabel(false, "Superset")).toBe("Export file");
    expect(connectorButtonLabel(true, "Power BI")).toBe("Power BI");
    expect(connectorButtonLabel(true, "Superset")).toBe("Superset");
  });
});
