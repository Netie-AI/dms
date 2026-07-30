import type { AnswerEnvelope, SpaceSummary } from "./types";

export const FIXTURE_SPACES: SpaceSummary[] = [
  { id: "sp_q3_audit", name: "Q3 Audit", source_count: 3, member_count: 4 },
  { id: "sp_margin", name: "Margin sandbox", source_count: 5, member_count: 2 },
];

export const SUGGESTED_QUESTIONS = [
  "What was Q3 revenue for the Northern region?",
  "Which workbook contributes the most to Northern revenue?",
  "Show me quarantined files from the last ingest.",
  "List silver tables in this Space.",
  "Who changed quantity on order 88201?",
  "Sum penalties across all active contracts.",
];

/** Fixture matching architecture §4.7 — every value id is clickable. */
export const FIXTURE_ANSWER: AnswerEnvelope = {
  answer_id: "ans_fixture_q3_north",
  text: "Q3 revenue for the Northern region was RM 4,203,881.44.",
  values: [
    {
      id: "v1",
      value: 4203881.44,
      unit: "MYR",
      label: "Q3 revenue",
    },
  ],
  badge: "L1_GOVERNED_METRIC",
  sql_used:
    "SELECT SUM(s.amount) FROM silver.sales s JOIN silver.customer c ON c.id = s.customer_id WHERE s.quarter = 'Q3' AND c.region = 'Northern'",
  assumptions: [
    "Q3 = 2026-07-01 to 2026-09-30",
    "excludes cancelled orders",
  ],
  as_of: "2026-07-30T09:14:00Z",
  contributing_sources: [
    {
      ref_id: "ref_q3_sales",
      container: "Q3_sales_final_v2.xlsx",
      member: "Data",
      kind: "xlsx",
      row_count: 1846,
      contribution: 2891004.1,
      origin_uri: "\\\\fs01\\finance\\2026\\Q3_sales_final_v2.xlsx",
    },
    {
      ref_id: "ref_kl_branch",
      container: "KL_branch_sales.xlsx",
      member: "Sheet1",
      kind: "xlsx",
      row_count: 402,
      contribution: 811277.34,
      origin_uri: "\\\\fs01\\finance\\2026\\KL_branch_sales.xlsx",
    },
    {
      ref_id: "ref_erp_inv",
      container: "erp.public.invoices",
      kind: "sql",
      row_count: 81953,
      contribution: 501600.0,
      origin_uri: "postgres://erp-prod",
    },
  ],
  drillthrough_token: "dt_fixture",
  audit_id: "aud_fixture_01",
};
