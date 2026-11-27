/**
 * Named abstain reason, same order as tests/redteam/rt_grader.py abstain_reason.
 * The only writer of ``abstain_reason`` is reserved_as_of_abstain
 * (packages/executor/dms_executor/envelope.py). Other names live in assumptions
 * (GEN-01 prefix from packages/executor/dms_executor/generative_ask.py) or text.
 * A clarify or access_needed block is read as structured fields, never from
 * the question sentence.
 */

import type { AccessNeeded, ClarifyBlock, ClarifyReading, GrantedSpace } from "@/lib/types";

const DEMOTION =
  /\((?:E\d|hard rule|ONTOLOGY-AUDIT|F32|E9-0|FF-0|ANS-0)|withheld|mismatch|\bpad\b|scope conflict|polarity/i;

const BENIGN_PREFIXES = [
  "GEN-01 ontology compile",
  "executed via Cortex submit after validate",
  "GEN-01 Cortex ontology_plan SQL",
  "live Cortex ask",
  "redteam-stub",
  "include:",
  "exclude:",
  "unsure:",
];

export type AbstainFields = {
  badge?: unknown;
  abstained?: unknown;
  abstain_reason?: unknown;
  assumptions?: unknown;
  text?: unknown;
  clarify?: unknown;
  access_needed?: unknown;
};

function fieldText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asReading(value: unknown): ClarifyReading | null {
  if (!value || typeof value !== "object") return null;
  const row = value as { reading_id?: unknown; label?: unknown };
  const reading_id = fieldText(row.reading_id);
  const label = fieldText(row.label);
  if (!reading_id || !label) return null;
  return { reading_id, label };
}

/** 1 to 3 readings with ids and labels. Anything else is not a clarify card. */
export function readClarify(env: AbstainFields): ClarifyBlock | null {
  const raw = env.clarify;
  if (!raw || typeof raw !== "object") return null;
  const block = raw as { clarify_id?: unknown; question?: unknown; readings?: unknown };
  const clarify_id = fieldText(block.clarify_id);
  const question = fieldText(block.question);
  if (!clarify_id || !question || !Array.isArray(block.readings)) return null;
  if (block.readings.length < 1 || block.readings.length > 3) return null;
  const readings: ClarifyReading[] = [];
  const seen = new Set<string>();
  for (const item of block.readings) {
    const row = asReading(item);
    if (!row || seen.has(row.reading_id)) return null;
    seen.add(row.reading_id);
    readings.push(row);
  }
  return { clarify_id, question, readings };
}

function asSpace(value: unknown): GrantedSpace | null {
  if (!value || typeof value !== "object") return null;
  const row = value as { id?: unknown; name?: unknown };
  const id = fieldText(row.id);
  const name = fieldText(row.name);
  if (!id || !name) return null;
  return { id, name };
}

/** The access card. can_request is true only for a boolean true. */
export function readAccess(env: AbstainFields): AccessNeeded | null {
  const raw = env.access_needed;
  if (!raw || typeof raw !== "object") return null;
  const block = raw as {
    table?: unknown;
    granted_in_spaces?: unknown;
    can_request?: unknown;
  };
  const table = fieldText(block.table);
  if (!table || !Array.isArray(block.granted_in_spaces) || block.granted_in_spaces.length < 1) {
    return null;
  }
  const granted_in_spaces: GrantedSpace[] = [];
  const seen = new Set<string>();
  for (const item of block.granted_in_spaces) {
    const row = asSpace(item);
    if (!row || seen.has(row.id)) return null;
    seen.add(row.id);
    granted_in_spaces.push(row);
  }
  return { table, granted_in_spaces, can_request: block.can_request === true };
}

/** The request button exists only when the route is allowed and still present. */
export function accessRequestVisible(canRequest: boolean, routeMissing: boolean): boolean {
  return canRequest && !routeMissing;
}

export function isAbstain(env: AbstainFields): boolean {
  return env.abstained === true || env.badge === "ABSTAIN";
}

function notesOf(assumptions: unknown): string[] {
  if (Array.isArray(assumptions)) {
    return assumptions.map((a) => String(a).trim()).filter(Boolean);
  }
  if (typeof assumptions === "string" && assumptions.trim()) return [assumptions.trim()];
  return [];
}

export function namedAbstainReason(env: AbstainFields): string {
  const direct = env.abstain_reason;
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  const clarify = readClarify(env);
  if (clarify) return clarify.question;
  const access = readAccess(env);
  if (access) return `ungranted:${access.table}`;
  const notes = notesOf(env.assumptions);
  for (const n of notes) {
    if (n.startsWith("GEN-01: ")) return n.slice("GEN-01: ".length);
  }
  for (const n of notes) {
    if (DEMOTION.test(n)) return n;
  }
  for (const n of notes) {
    if (!BENIGN_PREFIXES.some((b) => n.startsWith(b))) return n;
  }
  const first = String(env.text ?? "")
    .trim()
    .split("\n")[0];
  return first ? first.slice(0, 160) : "no_reason_given";
}
