import { describe, expect, it } from "vitest";
import { adjustCatalog, chooseChart, validateModelSpec } from "./chartSpec";

const rows = [
  { country: "MY", region: "W", day: "2026-01-01", spend: 1, qty: 2 },
  { country: "SG", region: "E", day: "2026-01-02", spend: 3, qty: 4 },
];

describe("chart spec validation", () => {
  it("drops a missing column and an invented number", () => {
    expect(validateModelSpec({ kind: "pie", x: "missing", y: ["spend"] }, rows)).toBeNull();
    expect(validateModelSpec({ kind: "bar", x: "country", y: ["nope"] }, rows)).toBeNull();
    const kept = validateModelSpec(
      { kind: "pie", x: "country", y: "spend", value: 99999 },
      rows,
    );
    expect(kept).toEqual({ kind: "pie", x: "country", y: ["spend"], series: null });
    expect(kept && "value" in kept).toBe(false);
  });

  it("accepts stacked, multi-line, and scatter only in a drawable shape", () => {
    expect(validateModelSpec({ kind: "scatter", x: "spend", y: ["spend"] }, rows)).toBeNull();
    expect(validateModelSpec({ kind: "scatter", x: "spend", y: ["qty"] }, rows)?.kind).toBe("scatter");
    expect(validateModelSpec({ kind: "stacked", x: "country", y: ["spend"] }, rows)).toBeNull();
    expect(
      validateModelSpec(
        { kind: "stacked", x: "country", y: ["spend"], series: "region" },
        rows,
      )?.series,
    ).toBe("region");
    expect(
      validateModelSpec({ kind: "multi_line", x: "day", y: ["spend", "qty"] }, rows)?.y,
    ).toEqual(["spend", "qty"]);
    const numbers = [
      { a: 1, b: 2 },
      { a: 3, b: 4 },
    ];
    expect(adjustCatalog(numbers).kinds).toEqual(["line", "scatter"]);
  });

  it("Adjust chooses a kind from the validated list", () => {
    const catalog = adjustCatalog(rows);
    expect(catalog.kinds).toEqual(["bar", "line", "pie", "stacked", "multi_line", "scatter"]);
    expect(chooseChart(rows, catalog, { kind: "pie" })).toEqual({
      kind: "pie",
      x: "country",
      y: ["spend"],
      series: null,
    });
    expect(chooseChart(rows, catalog, { kind: "scatter" })?.kind).toBe("scatter");
    expect(chooseChart(rows, catalog, { kind: "hbar" })).toBeNull();
    expect(
      chooseChart(rows, catalog, { kind: "pie", x: "missing", repairX: false }),
    ).toBeNull();
  });
});
