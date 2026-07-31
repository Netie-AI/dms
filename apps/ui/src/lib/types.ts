/** Answer envelope — architecture §4.7 */

export type BadgeKind =
  | "L0_CERTIFIED"
  | "L1_GOVERNED_METRIC"
  | "L2_VALIDATED"
  | "L2_ANOMALOUS"
  | "ABSTAIN";

export type AnswerValue = {
  id: string;
  value: number;
  unit?: string;
  label: string;
};

export type ContributingSource = {
  ref_id: string;
  container: string;
  member?: string;
  kind: "xlsx" | "csv" | "sql" | "parquet" | "pdf" | "api";
  row_count: number;
  contribution: number;
  origin_uri?: string;
};

export type ChartSpec = {
  kind: "bar" | "hbar";
  x: string;
  y: string;
  title?: string;
};

export type AnswerEnvelope = {
  answer_id: string;
  text: string;
  values: AnswerValue[];
  badge: BadgeKind;
  /** Phase 0 — must lockstep with badge===ABSTAIN */
  abstained?: boolean;
  sql_used?: string;
  assumptions: string[];
  as_of: string;
  contributing_sources: ContributingSource[];
  drillthrough_token?: string;
  audit_id?: string;
  ask_mode?: "demo" | "live";
  demo_fallback_used?: boolean;
  /** E6 — required when demo_fallback_used is true */
  demo_fallback_banner?: boolean;
  space_id?: string | null;
  rows?: Record<string, unknown>[];
  chart?: ChartSpec;
  suggestions?: string[];
};

export type SpaceSummary = {
  id: string;
  name: string;
  source_count: number;
  member_count: number;
};

export type AppRole = "viewer" | "steward" | "admin";

export type NavId =
  | "chat"
  | "library"
  | "studio"
  | "amend"
  | "audit"
  | "runs"
  | "admin";
