/** CCA-07 — read side of the constraint cascade trace (CCA-01 schema).
 *
 * This file renders what the envelope says and nothing else. It must never
 * decide, infer or repair a stage status: the cascade runs in the executor
 * (packages/executor/dms_executor/constraint_cascade.py), and a second
 * orchestrator in the browser is exactly how a UI ends up painting green over
 * an engine that abstained.
 */

/** Fixed cascade order. Stages are displayed in this order, never arrival order. */
export const STAGE_ORDER = [
  "sense",
  "asset_class",
  "geo",
  "grain",
  "ontology",
  "sql",
  "envelope",
] as const;

export type StageType = (typeof STAGE_ORDER)[number];

export const STAGE_STATUSES = ["CERTIFIED", "ABSTAIN", "REFUSE"] as const;

export type StageStatus = (typeof STAGE_STATUSES)[number];

/** One CCA-01 constraint, mirroring parse_constraint's output shape exactly. */
export type ConstraintStage = {
  constraint_id: string;
  type: StageType;
  candidate: string;
  binding: string | null;
  evidence: string[];
  status: StageStatus;
  reasons: string[];
};

export const STAGE_LABELS: Record<StageType, string> = {
  sense: "Sense",
  asset_class: "Asset class",
  geo: "Geo",
  grain: "Grain/measure",
  ontology: "Ontology verify",
  sql: "SQL",
  envelope: "Envelope",
};

export const STATUS_LABELS: Record<StageStatus, string> = {
  CERTIFIED: "CERTIFIED",
  ABSTAIN: "ABSTAIN",
  REFUSE: "REFUSE",
};

const STAGE_INDEX = new Map<string, number>(STAGE_ORDER.map((s, i) => [s, i]));

function isStageType(value: unknown): value is StageType {
  return typeof value === "string" && STAGE_INDEX.has(value);
}

function isStageStatus(value: unknown): value is StageStatus {
  return (
    typeof value === "string" && (STAGE_STATUSES as readonly string[]).includes(value)
  );
}

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((x) => typeof x === "string") ? [...(value as string[])] : null;
}

function parseStage(raw: unknown): ConstraintStage | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const item = raw as Record<string, unknown>;
  // An unknown type or status is dropped, never mapped onto a neighbouring
  // value: coercion here would let an engine field we do not understand read
  // as CERTIFIED on a steward's screen.
  if (!isStageType(item.type) || !isStageStatus(item.status)) return null;
  const constraintId = typeof item.constraint_id === "string" ? item.constraint_id.trim() : "";
  if (!constraintId) return null;
  if (typeof item.candidate !== "string") return null;
  const binding = item.binding;
  if (binding !== null && typeof binding !== "string") return null;
  const evidence = stringList(item.evidence);
  const reasons = stringList(item.reasons);
  if (evidence === null || reasons === null) return null;
  return {
    constraint_id: constraintId,
    type: item.type,
    candidate: item.candidate,
    binding,
    evidence,
    status: item.status,
    reasons,
  };
}

type ParsedTrace = { stages: ConstraintStage[]; dropped: number };

function parseTraceItems(raw: unknown): ParsedTrace {
  if (!Array.isArray(raw)) return { stages: [], dropped: 0 };
  const stages: ConstraintStage[] = [];
  const seen = new Set<StageType>();
  let dropped = 0;
  for (const item of raw) {
    const stage = parseStage(item);
    // A repeated stage is a schema violation in the executor too. Keep the
    // first report and count the rest as unreadable rather than showing two
    // verdicts for one stage.
    if (stage === null || seen.has(stage.type)) {
      dropped += 1;
      continue;
    }
    seen.add(stage.type);
    stages.push(stage);
  }
  stages.sort((a, b) => (STAGE_INDEX.get(a.type) ?? 0) - (STAGE_INDEX.get(b.type) ?? 0));
  return { stages, dropped };
}

/** Stages the envelope actually reported, in fixed cascade order. */
export function parseConstraintTrace(raw: unknown): ConstraintStage[] {
  return parseTraceItems(raw).stages;
}

/** Trace items that could not be read. Shown so a dropped stage is visible
 *  as a gap rather than silently absent. */
export function countMalformedStages(raw: unknown): number {
  return parseTraceItems(raw).dropped;
}

export type TraceSummary = {
  /** Stages the envelope reported. 0 means no cascade ran, not "all clear". */
  ran: number;
  certified: number;
  /** Stage type of the first non-CERTIFIED stage, else null. */
  blockedAt: StageType | null;
  blockedReason: string;
};

export function summariseTrace(stages: ConstraintStage[]): TraceSummary {
  const certified = stages.filter((s) => s.status === "CERTIFIED").length;
  const blocked = stages.find((s) => s.status !== "CERTIFIED") ?? null;
  return {
    ran: stages.length,
    certified,
    blockedAt: blocked ? blocked.type : null,
    blockedReason: blocked
      ? blocked.reasons.filter((r) => r.trim()).join("; ") ||
        "No reason given by the cascade."
      : "",
  };
}

/** One sentence for the panel header. An empty trace says so; it never
 *  degrades into a claim that every stage passed. */
export function traceHeadline(summary: TraceSummary): string {
  if (summary.ran === 0) {
    return "No cascade ran. Nothing here is certified.";
  }
  if (summary.blockedAt) {
    return `Blocked at ${STAGE_LABELS[summary.blockedAt]}: ${summary.blockedReason}`;
  }
  return `${summary.certified} of ${STAGE_ORDER.length} stages certified.`;
}

export type StageTone = "ok" | "warn" | "danger";

export function toneForStatus(status: StageStatus): StageTone {
  if (status === "CERTIFIED") return "ok";
  return status === "REFUSE" ? "danger" : "warn";
}
