import type { InsightItem } from "./insightProof";

/** Fixture B from tests/test_insight_proof_03_register.py, as the route returns it. */
export const fixtureItem: InsightItem = {
  envelope: {
    answer_id: "ins_156d4c0dd99d3b10",
    text: "2 of 4 Territory.name values (North, South) carry 86.4 pct of order_amount",
    badge: "L1_GOVERNED_METRIC",
    abstained: false,
    sql_used: "WITH g AS (SELECT ...) SELECT g.\"Territory_name\" AS label, ...",
    audit_id: "led_000123",
    rows: [
      { label: "North", value: 900, share_pct: 81.8, cum_share_pct: 81.8, grouped_total: 1100 },
      { label: "South", value: 50, share_pct: 4.5, cum_share_pct: 86.4, grouped_total: 1100 },
      { label: "East", value: 30, share_pct: 2.7, cum_share_pct: 89.1, grouped_total: 1100 },
      { label: "West", value: 20, share_pct: 1.8, cum_share_pct: 90.9, grouped_total: 1100 },
      { label: null, value: 100, share_pct: 9.1, cum_share_pct: 100, grouped_total: 1100 },
    ],
  },
  proof: {
    insight_id: "ins_156d4c0dd99d3b10",
    kind: "concentration",
    raw_total: 1100,
    grouped_total: 1100,
    tolerance: 0.03,
    conserves: true,
    result_hash: "f031665dc7fbfd5b5f50e9746a8655b3fefe72033791105f6f29e23430b17195",
    reexec_result_hash: "f031665dc7fbfd5b5f50e9746a8655b3fefe72033791105f6f29e23430b17195",
    executions: [
      {
        engine: "submit",
        result_hash: "f031665dc7fbfd5b5f50e9746a8655b3fefe72033791105f6f29e23430b17195",
        row_count: 5,
      },
      {
        engine: "submit",
        result_hash: "f031665dc7fbfd5b5f50e9746a8655b3fefe72033791105f6f29e23430b17195",
        row_count: 5,
      },
    ],
    ledger: { event_type: "insight.proven", entry_id: "led_000123", seq: 7 },
    definition: {
      measure: "order_amount",
      expression: "ROUND(SUM(f.amount), 2)",
      item_id: "item_measure_order_amount",
      version_id: "ver_sales_b1",
      source: "approved_store",
    },
    watermarks: { orders: "2026-10-01T00:00:00Z", territory: "2026-10-01T00:00:00Z" },
    drift: null,
  },
};
