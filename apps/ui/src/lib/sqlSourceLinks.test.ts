import { describe, expect, it } from "vitest";
import { sqlLinksLabel } from "./sqlSourceLinks";

describe("sqlLinksLabel", () => {
  it("does not call an empty FK set verified", () => {
    expect(
      sqlLinksLabel({
        verified: false,
        measured: false,
        reason: "the source declares no foreign keys, so there is nothing to measure",
        links: [],
        violations: [],
      }),
    ).toContain("no foreign keys");
  });

  it("names violations instead of a green join", () => {
    expect(
      sqlLinksLabel({
        verified: false,
        measured: true,
        links: [
          {
            name: "FK_Orders_Customers",
            from: "orders",
            to: "customers",
            cardinality: "unverified",
          },
        ],
        violations: [
          {
            check: "link_intact",
            subject: "FK_Orders_Customers",
            detail: "orphans",
          },
        ],
        capped_tables: ["bronze.dbo_customers"],
      }),
    ).toBe(
      "1 link(s) measured, 1 violation(s). Joins stay unverified. Cap hit: bronze.dbo_customers.",
    );
  });

  it("says verified only when the receipt did", () => {
    expect(
      sqlLinksLabel({
        verified: true,
        measured: true,
        links: [
          {
            name: "FK_Orders_Customers",
            from: "orders",
            to: "customers",
            cardinality: "n_to_1",
          },
        ],
        violations: [],
      }),
    ).toBe("1 link(s) verified.");
  });
});
