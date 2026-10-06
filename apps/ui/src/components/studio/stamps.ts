/**
 * Stamp slots the ask envelope can carry. A missing key renders "not stamped".
 * This module never fills a value the object does not already hold.
 *
 * served_provider, served_model, served_local, learn_enabled, learn_source,
 * route_store_id: packages/executor/dms_executor/generative_ask.py SETUP_FIELD_KEYS
 * served_attribution, generate_legs: with_served_attribution in that file
 * model_calls, lane: Executor.live_ask in packages/executor/dms_executor/__init__.py
 * plan_source, plan_origin: with_plan_source / with_plan_origin
 * engine_as_of*: stamp_engine_clock in packages/executor/dms_executor/demo_warehouse.py
 *
 * Cortex pin: no ask-envelope writer. Score scans use cortex_sha
 * (scripts/score_curated.py). Shown only when that key is already present.
 * Contract version: health body key `contract`
 * (apps/api/dms_api/routes/health.py), not copied onto the ask envelope.
 * Timings: no duration key is written on the ask envelope.
 */

export const NOT_STAMPED = "not stamped";

export type StampSlot = {
  id: string;
  label: string;
  keys: readonly string[];
};

export const STAMP_SLOTS: readonly StampSlot[] = [
  { id: "served_provider", label: "served_provider", keys: ["served_provider"] },
  { id: "served_model", label: "served_model", keys: ["served_model"] },
  { id: "served_local", label: "served_local", keys: ["served_local"] },
  { id: "served_attribution", label: "served_attribution", keys: ["served_attribution"] },
  { id: "learn_enabled", label: "learn_enabled", keys: ["learn_enabled"] },
  { id: "learn_source", label: "learn_source", keys: ["learn_source"] },
  { id: "route_store_id", label: "route_store_id", keys: ["route_store_id"] },
  { id: "model_calls", label: "model_calls", keys: ["model_calls"] },
  { id: "generate_legs", label: "generate_legs", keys: ["generate_legs"] },
  { id: "plan_source", label: "plan_source", keys: ["plan_source"] },
  { id: "plan_origin", label: "plan_origin", keys: ["plan_origin"] },
  { id: "lane", label: "lane", keys: ["lane"] },
  { id: "engine_as_of", label: "engine_as_of", keys: ["engine_as_of"] },
  { id: "engine_as_of_after", label: "engine_as_of_after", keys: ["engine_as_of_after"] },
  { id: "engine_timezone", label: "engine_timezone", keys: ["engine_timezone"] },
  {
    id: "engine_timezone_after",
    label: "engine_timezone_after",
    keys: ["engine_timezone_after"],
  },
  { id: "cortex-pin", label: "Cortex pin", keys: ["cortex_sha"] },
  { id: "contract-version", label: "Contract version", keys: ["contract"] },
  { id: "timings", label: "Timings", keys: [] },
];

const NAMED_IDS = new Set(["cortex-pin", "contract-version", "timings"]);

function formatStamp(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return NOT_STAMPED;
  }
}

export function readStamp(env: object, slot: StampSlot): string {
  const rec = env as Record<string, unknown>;
  for (const key of slot.keys) {
    if (!Object.prototype.hasOwnProperty.call(rec, key)) continue;
    const value = rec[key];
    if (value === undefined) continue;
    return formatStamp(value);
  }
  return NOT_STAMPED;
}

/** Fixed slots, plus any other served_* key the envelope already has. */
export function stampSlotsFor(env: object): StampSlot[] {
  const known = new Set(STAMP_SLOTS.flatMap((slot) => slot.keys));
  const extras = Object.keys(env as Record<string, unknown>)
    .filter((key) => key.startsWith("served_") && !known.has(key))
    .sort()
    .map((key) => ({ id: key, label: key, keys: [key] }));
  const written = STAMP_SLOTS.filter((slot) => !NAMED_IDS.has(slot.id));
  const named = STAMP_SLOTS.filter((slot) => NAMED_IDS.has(slot.id));
  return [...written, ...extras, ...named];
}
