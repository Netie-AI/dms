import { describe, expect, it } from "vitest";
import { pieSlices, scatterPoints } from "./chartSpec";

describe("pieSlices", () => {
  const rows = [
    { warehouse: "Shah Alam", stock_kg: 300 },
    { warehouse: "Klang", stock_kg: 100 },
  ];

  it("reads values from rows and splits the circle by share", () => {
    const s = pieSlices(rows, "warehouse", "stock_kg");
    expect(s.map((x) => x.label)).toEqual(["Shah Alam", "Klang"]);
    expect(s.map((x) => x.value)).toEqual([300, 100]);
    expect(s[0].share).toBeCloseTo(0.75);
    expect(s[0].startAngle).toBe(0);
    expect(s[0].endAngle).toBeCloseTo(1.5 * Math.PI);
    expect(s[1].endAngle).toBeCloseTo(2 * Math.PI);
  });

  it("drops non-finite and non-positive cells instead of drawing zero", () => {
    const s = pieSlices(
      [
        ...rows,
        { warehouse: "Penang", stock_kg: Number.NaN },
        { warehouse: "Johor", stock_kg: "n/a" },
        { warehouse: "Ipoh", stock_kg: null },
        { warehouse: "Kuantan", stock_kg: Number.POSITIVE_INFINITY },
        { warehouse: "Melaka", stock_kg: -5 },
      ],
      "warehouse",
      "stock_kg",
    );
    expect(s.map((x) => x.label)).toEqual(["Shah Alam", "Klang"]);
  });

  it("returns nothing when no slice is drawable", () => {
    expect(pieSlices([{ a: "x", b: "n/a" }], "a", "b")).toEqual([]);
    expect(pieSlices([], "a", "b")).toEqual([]);
  });
});

describe("scatterPoints", () => {
  it("pairs finite x/y cells and keeps the source row index", () => {
    const pts = scatterPoints(
      [
        { qty_kg: 10, revenue_myr: 55.5 },
        { qty_kg: Number.NaN, revenue_myr: 1 },
        { qty_kg: "30", revenue_myr: 160 },
        { qty_kg: 40, revenue_myr: undefined },
      ],
      "qty_kg",
      "revenue_myr",
    );
    expect(pts).toEqual([
      { x: 10, y: 55.5, row: 0 },
      { x: 30, y: 160, row: 2 },
    ]);
  });
});
