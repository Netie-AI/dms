import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SqlSourceReceiptView } from "./SqlSourcePanel";
import type { SqlSourceReceipt } from "../lib/api";

const receipt: SqlSourceReceipt = {
  source: "sqlserver://db.example.net:1433/sales",
  tables: [
    {
      bronze_table: "bronze.dbo_customers",
      source: "sqlserver://db.example.net:1433/sales#dbo.customers",
      row_count: 2,
      truncated: true,
      extracted_at: "2026-09-06T12:00:00.000000Z",
    },
    {
      bronze_table: "bronze.dbo_orders",
      source: "sqlserver://db.example.net:1433/sales#dbo.orders",
      row_count: 2,
      truncated: false,
      extracted_at: "2026-09-06T12:00:00.000000Z",
    },
  ],
  skipped: [],
  declared_primary_keys: 2,
  declared_foreign_keys: 1,
  links: {
    verified: false,
    measured: true,
    links: [
      {
        name: "FK_Orders_Customers",
        from: "dbo.orders",
        from_columns: ["cust_ref"],
        to: "dbo.customers",
        to_columns: ["cust_ref"],
        cardinality: "unverified",
        max_fanout: 0,
      },
    ],
    violations: [
      {
        check: "fk_intact",
        subject: "FK_Orders_Customers",
        detail:
          "2 orphan rows (2 distinct keys) on dbo.orders; parent dbo.customers was capped by max_rows",
      },
    ],
  },
};

describe("SqlSourceReceiptView", () => {
  it("shows extract success, truncated, and names the failed link", () => {
    const html = renderToStaticMarkup(createElement(SqlSourceReceiptView, { receipt }));
    expect(html).toContain('data-testid="sql-source-receipt"');
    expect(html).toContain("bronze.dbo_customers");
    expect(html).toContain("capped at max_rows");
    expect(html).toContain('data-testid="sql-source-truncated"');
    expect(html).toContain("extracted_at 2026-09-06T12:00:00.000000Z");
    expect(html).toContain("FK_Orders_Customers");
    expect(html).toContain("unverified");
    expect(html).toContain("max_rows");
    expect(html).toContain("Extract still succeeded");
    expect(html).toContain('data-testid="sql-source-failed-links"');
  });

  it("does not claim verified when nothing was measured", () => {
    const html = renderToStaticMarkup(
      createElement(SqlSourceReceiptView, {
        receipt: {
          ...receipt,
          tables: [{ ...receipt.tables[1], truncated: false }],
          links: {
            verified: false,
            measured: false,
            reason: "the source declares no foreign keys, so there is nothing to measure",
            links: [],
            violations: [],
          },
        },
      }),
    );
    expect(html).toContain("no foreign keys");
    expect(html).not.toContain("sql-source-failed-links");
    expect(html).not.toContain("truncated");
  });
});
