import { describe, expect, it } from "vitest";
import {
  asObjectIdent,
  bronzeRelationFromPreview,
  objectDraftFromBronze,
} from "./objectDraft";

describe("bronzeRelationFromPreview", () => {
  it("uses a qualified bronze table from the tree", () => {
    expect(
      bronzeRelationFromPreview({ kind: "bronze", table: "bronze.01_clean_sales" }),
    ).toBe("bronze.01_clean_sales");
  });

  it("does not publish warehouse demo tables as objects", () => {
    expect(
      bronzeRelationFromPreview({ kind: "warehouse", table: "inventory" }),
    ).toBeNull();
  });
});

describe("objectDraftFromBronze", () => {
  it("prefixes a digit-leading stem so the object name is legal", () => {
    const d = objectDraftFromBronze("bronze.01_clean_sales", ["sku", "qty", "_src"]);
    expect(d.name).toBe("c_01_clean_sales");
    expect(d.aliases).toEqual({ sku: "sku", qty: "qty" });
    expect(d.toolSql).toContain("objects.c_01_clean_sales");
  });

  it("keeps a letter-starting stem", () => {
    expect(asObjectIdent("orders", "object")).toBe("orders");
  });

  it("drafts GROUP BY SQL when preview has a category and a measure", () => {
    const d = objectDraftFromBronze(
      "bronze.orders",
      ["warehouse", "qty", "_src"],
      [
        { warehouse: "WH-A", qty: "12" },
        { warehouse: "WH-B", qty: "8" },
        { warehouse: "WH-A", qty: 3 },
      ],
    );
    expect(d.toolSql).toBe(
      "SELECT warehouse, SUM(CAST(qty AS DOUBLE)) AS qty FROM objects.orders GROUP BY warehouse",
    );
  });

  it("keeps COUNT when there is no numeric measure", () => {
    const d = objectDraftFromBronze("bronze.sales_sku", ["sku_id", "name"], [
      { sku_id: "1", name: "A" },
      { sku_id: "2", name: "B" },
    ]);
    expect(d.toolSql).toContain("COUNT(*)");
  });
});