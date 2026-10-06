/**
 * Named abstain reason, same order as tests/redteam/rt_grader.py abstain_reason.
 * The only writer of ``abstain_reason`` is reserved_as_of_abstain
 * (packages/executor/dms_executor/envelope.py). Other names live in assumptions
 * (GEN-01 prefix from packages/executor/dms_executor/generative_ask.py) or text.
 */

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
};

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
