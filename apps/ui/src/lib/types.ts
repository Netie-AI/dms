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

export type AnswerEnvelope = {
  answer_id: string;
  text: string;
  values: AnswerValue[];
  badge: BadgeKind;
  sql_used?: string;
  assumptions: string[];
  as_of: string;
  contributing_sources: ContributingSource[];
  drillthrough_token?: string;
  audit_id?: string;
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
