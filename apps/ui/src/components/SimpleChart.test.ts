import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SimpleChart } from "./SimpleChart";

describe("SimpleChart", () => {
  it("renders a pie legend from grouped rows", () => {
    const html = renderToStaticMarkup(
      createElement(SimpleChart, {
        chart: { kind: "pie", x: "warehouse", y: "stock_on_hand", title: "Stock by warehouse" },
        rows: [
          { warehouse: "WH-A", stock_on_hand: 12 },
          { warehouse: "WH-B", stock_on_hand: 8 },
        ],
      }),
    );
    expect(html).toContain('role="img"');
    expect(html).toContain("Stock by warehouse");
    expect(html).toContain("WH-A");
    expect(html).toContain("WH-B");
    expect(html).toContain("<path ");
  });

  it("renders an hbar from ranked rows", () => {
    const html = renderToStaticMarkup(
      createElement(SimpleChart, {
        chart: { kind: "hbar", x: "warehouse", y: "stock_on_hand", title: "Stock by warehouse" },
        rows: [
          { warehouse: "WH-A", stock_on_hand: 12 },
          { warehouse: "WH-B", stock_on_hand: 8 },
        ],
      }),
    );
    expect(html).toContain("Stock by warehouse");
    expect(html).toContain("WH-A");
    expect(html).toContain("12");
  });

  it("renders a line from dated rows", () => {
    const html = renderToStaticMarkup(
      createElement(SimpleChart, {
        chart: { kind: "line", x: "month", y: "revenue", title: "Revenue" },
        rows: [
          { month: "2024-01", revenue: 10 },
          { month: "2024-02", revenue: 12 },
        ],
      }),
    );
    expect(html).toContain("Revenue");
    expect(html).toContain("<polyline");
    expect(html).toContain("2024-01");
  });

  it("renders a bignum from a single certified metric", () => {
    const html = renderToStaticMarkup(
      createElement(SimpleChart, {
        chart: { kind: "bignum", y: "stock_on_hand", label: "stock_on_hand", value: 42 },
        rows: [{ stock_on_hand: 42 }],
      }),
    );
    expect(html).toContain("stock_on_hand");
    expect(html).toContain("42");
  });
});
