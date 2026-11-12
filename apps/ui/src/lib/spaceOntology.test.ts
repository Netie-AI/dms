import { describe, expect, it } from "vitest";
import { linkSentence, neighbourhood, violationText } from "./spaceOntology";

const edges = [
  { id: "e1", source: "order", target: "customer", source_property: "customer_id", target_property: "id", cardinality: "many_to_one" },
  { id: "e2", source: "order_line", target: "order", source_property: "order_id", target_property: "id", cardinality: "many_to_one" },
  { id: "e3", source: "payment", target: "invoice", source_property: "invoice_id", target_property: "id", cardinality: "many_to_one" },
];

describe("neighbourhood", () => {
  it("returns links on either end, naming the other object", () => {
    const n = neighbourhood("order", edges);
    expect(n.map((x) => x.other).sort()).toEqual(["customer", "order_line"]);
    expect(n.find((x) => x.other === "customer")?.via).toBe("order.customer_id → customer.id");
    expect(n.find((x) => x.other === "order_line")?.via).toBe("order_line.order_id → order.id");
  });
  it("is empty for an object with no links", () => {
    expect(neighbourhood("lonely", edges)).toEqual([]);
  });
});

describe("space ontology wording", () => {
  it("names child, parent and columns", () => {
    expect(
      linkSentence({ name: "fk", from: "orders", from_columns: ["cust_id"], to: "customers", to_columns: ["id"], cardinality: "many_to_one", max_fanout: 1, status: "verified", violations: [] }),
    ).toBe("orders → customers on cust_id = id");
  });
  it("does not invent columns for an unlanded link", () => {
    expect(
      linkSentence({ name: "fk", from: "a", from_columns: [], to: "b", to_columns: [], cardinality: "unverified", max_fanout: 0, status: "unverified", violations: [] }),
    ).toBe("a → b on unknown columns");
  });
  it("puts the violation in words", () => {
    expect(violationText({ check: "no_primary_key", subject: "t", detail: "no row identity" })).toBe(
      "No primary key: no row identity",
    );
  });
});
