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
  /** Doc-RAG chunk excerpt — not SQL row drillthrough */
  snippet?: string;
  chunk_index?: number;
  space_id?: string;
  /** Lake provenance (SQLSRC-05). Distinct from kind (xlsx/csv/sql/...). */
  extracted_at?: string | null;
  source_kind?: "sql" | "file" | null;
};

export type ChartSpec = {
  kind: "bar" | "hbar" | "line" | "bignum";
  x?: string;
  y?: string;
  title?: string;
  /** bignum only — scalar already present in rows */
  value?: number | string;
  label?: string;
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
  grounded_tables?: string[];
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
  | "spaces"
  | "library"
  | "studio"
  | "ontology"
  | "amend"
  | "audit"
  | "trust"
  | "runs"
  | "admin";

/* ── Ontology — the shared vocabulary, authored in Cortex, rendered here ── */

export type OntologyProperty = {
  name: string;
  type: string;
  /** false = withheld from the agent (was semantic_layer sensitive_columns) */
  agent_visible: boolean;
};

export type OntologyObject = {
  id: string;
  description: string;
  primary_key: string;
  properties: OntologyProperty[];
  property_count: number;
  sensitive_count: number;
  metric_ids: string[];
};

export type OntologyLink = {
  id: string;
  from_object: string;
  from_property: string;
  to_object: string;
  to_property: string;
  cardinality: string;
};

export type OntologyAction = {
  id: string;
  /** "tool" = invocable governed write · "event" = registered ledger event */
  kind: string;
  description: string;
  ledger_event_type: string;
  object_type: string | null;
  required_role: string | null;
  requires_confirm: boolean;
  params: string[];
};

export type OntologyFunction = {
  id: string;
  description: string;
  module: string;
  callable: string;
};

export type OntologyMetric = {
  id: string;
  kind: string;
  synonyms: string[];
  result_columns: string[];
  params: string[];
  object_types: string[];
  sql: string;
};

export type OntologyCounts = {
  object_types: number;
  properties: number;
  sensitive_properties: number;
  link_types: number;
  action_types: number;
  action_tools: number;
  action_events: number;
  functions: number;
  metrics: number;
};

export type OntologyGraphNode = {
  id: string;
  label: string;
  description: string;
  primary_key: string;
  property_count: number;
  sensitive_count: number;
  metric_count: number;
  degree: number;
};

export type OntologyGraphEdge = {
  id: string;
  source: string;
  target: string;
  source_property: string;
  target_property: string;
  cardinality: string;
};

/** Every ontology read degrades to `ok: false` + hint when Cortex is down. */
export type Degradable = { ok: boolean; error?: string; hint?: string };

export type OntologySummary = Degradable & {
  pack?: string;
  counts?: OntologyCounts;
  objects_without_metrics?: string[];
};

export type OntologyBundle = {
  summary: OntologySummary;
  objects: OntologyObject[];
  links: OntologyLink[];
  actions: OntologyAction[];
  functions: OntologyFunction[];
  metrics: OntologyMetric[];
  graph: { nodes: OntologyGraphNode[]; edges: OntologyGraphEdge[] };
};

/* ── Trust — the evidence behind the badges ── */

export type TrustTotals = {
  total?: number;
  correct?: number;
  wrong?: number;
  abstain?: number;
  error?: number;
  pass?: number;
  fail?: number;
  robustness?: number;
  envelope_errors?: number;
};

export type TrustRun = {
  id: string;
  label: string;
  proves: string;
  present: boolean;
  file: string;
  generated_at?: string | null;
  mode?: string | null;
  totals?: TrustTotals;
  passed?: boolean | null;
  threshold_violations?: unknown[];
  rates?: { correct?: number | null; wrong?: number | null; abstain?: number | null; error?: number | null };
  by_category?: Record<string, TrustTotals>;
  tiers?: Record<string, TrustTotals>;
  corpus?: CorpusSizes;
};

export type TrustClaim = {
  statement: string;
  supported: boolean;
  blockers: string[];
  /** Human-verified items only — the denominator the claim may use. */
  corpus_n?: number;
  /** Everything scored, verified or not. Never a substitute for corpus_n. */
  corpus_expanded_n?: number;
  corpus_unverified_n?: number;
  corpus_target?: number;
  confidently_wrong?: number;
  phase?: string;
};

export type CorpusSizes = {
  expanded_n: number;
  claim_n: number;
  seed_n: number;
  unverified_n: number;
  claim_totals?: TrustTotals;
  expanded_totals?: TrustTotals;
};

export type AskPathScore = {
  kind?: string;
  pack: string;
  precision_on_answered: number;
  coverage_pct: number;
  correct: number;
  answered: number;
  wrong: number;
  total: number;
  abstained?: number;
  passed: boolean;
};

export type TrustSummary = Degradable & {
  thresholds?: Record<string, unknown>;
  runs?: Record<string, TrustRun>;
  claim: TrustClaim;
  ask_path?: AskPathScore[];
};

export type TrustRunDetailItem = {
  id?: string;
  category?: string;
  persona?: string;
  outcome?: string;
  route?: string;
  question?: string;
  raw_question?: string;
  detail?: string;
  sql_used?: string;
};

/* ── Runs and Admin — durable state, or an honest empty ── */

export type RunRecord = {
  id: string;
  kind: "ingest" | "query";
  status: string;
  created_at: string | null;
  space_name: string | null;
  detail: string;
  ledger_seq?: number | null;
  reasons: { file: string; reason: string; fix: string }[];
};

export type RunsBody = {
  configured: boolean;
  hint?: string;
  runs: RunRecord[];
  counts?: Record<string, number>;
};

export type AdminOverview = {
  configured: boolean;
  hint?: string;
  tenant_id: string;
  actor_role: string;
  users: { id: string; email: string; display_name: string | null; role: string; department: string | null }[];
  departments: { id: string; name: string }[];
  roles: { name: string; description: string | null }[];
  grants: {
    id: string;
    resource_kind: string;
    resource_id: string;
    permission: string;
    principal: string;
    principal_kind: string;
  }[];
  pools: { id: string; name: string; kind: string; config: Record<string, unknown> }[];
};

export type LibrarySource = {
  id: string;
  kind: string;
  ref: string;
  scope: string;
  space_id: string | null;
  space_name: string | null;
};
