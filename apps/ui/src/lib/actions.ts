/**
 * Governed actions client (ACT-03) for the ACT-02 routes under
 * /v1/amend/actions. Plain fetch, as AmendPage already uses, so the held
 * api.ts / types.ts stay untouched.
 *
 * The routes exist only while the server runs with DMS_ACTIONS=1; otherwise
 * every path 404s. probeActions() turns that into 'on' / 'off' and the page
 * renders nothing new unless it is 'on'.
 */

const BASE = "/api/v1/amend/actions";

export const DEFAULT_ACTION_TYPE = "inventory.set_reorder_level";

export type ActionsAvailability = "on" | "off";

export type ObjectKey = Record<string, string>;

export type CellState = {
  value: string | null;
  row_version: number;
  source_value: string | null;
};

export type ActionEvidence = {
  audit_id?: string;
  question?: string;
  badge?: string;
  row_count?: number;
  reverts_decision_id?: string;
  decline_reason?: string;
};

/** The stored diff of an action proposal (dms_core actions.propose_action). */
export type ActionDiff = {
  kind: "action";
  action_type: string;
  object_key: ObjectKey;
  value: string | null;
  op: "set" | "clear" | string;
  reason: string;
  object_type: string;
  field: string;
  before: CellState;
  expected_before_hash: string;
  expected_after_hash: string;
  spec_sha: string;
  source_kind: string;
  evidence: ActionEvidence;
  summary: string;
};

export type ActionProposal = {
  proposal_id: string;
  version_num: number;
  status: string;
  idempotency_token: string;
  diff: ActionDiff;
  created_at?: string | null;
};

export type ActionMutation = {
  executed: boolean;
  target?: string;
  source_system_changed?: boolean;
  detail?: string;
};

export type ChainReadback = {
  ok: boolean | null;
  first_break?: number | string | null;
  reason?: string;
};

/** The apply receipt (dms_api routes/actions.apply_action_route). */
export type ActionReceipt = {
  decision_id: string;
  proposal_id: string;
  version_num: number;
  status: string;
  action_type: string;
  object: { object_type: string; object_key: ObjectKey };
  field: string;
  op: string;
  before: CellState | null;
  after: CellState | null;
  before_hash: string;
  after_hash: string;
  verified: boolean;
  rows_changed: number;
  approval_ref: string;
  evidence: ActionEvidence | null;
  identity: string;
  separation_of_duties: { proven: boolean; reason?: string };
  ledger_entry_id: string | null;
  ledger_seq?: number | null;
  chain?: ChainReadback;
  source_kind: string;
  summary?: string;
  mutation: ActionMutation;
};

export type ProposeResult = {
  proposal_id: string;
  version_num: number;
  idempotency_token: string;
  status: string;
  diff: ActionDiff;
  mutation: ActionMutation;
  reverts_decision_id?: string;
};

export type DeclineResult = {
  decision_id: string;
  proposal_id: string;
  version_num: number;
  status: "rejected" | string;
  ledger_entry_id: string;
  mutation: ActionMutation;
};

export type ActionOverride = {
  object_type: string;
  object_key: ObjectKey;
  field: string;
  value: string | null;
  row_version: number;
  updated_at: string | null;
  last_decision_id: string | null;
};

/** One dms.action_decisions row (action_queries.decision_json). */
export type ActionDecision = {
  id: string;
  space_id: string | null;
  proposal_id: string;
  proposal_version_id: string;
  decision: "applied" | "declined" | string;
  action_type: string;
  object_type: string;
  object_key: ObjectKey;
  field: string;
  op: string;
  before: CellState | null;
  after: CellState | null;
  before_hash: string | null;
  after_hash: string | null;
  rows_changed: number;
  verified: boolean;
  approval_ref: string | null;
  evidence: ActionEvidence | null;
  cortex_entry_id: string | null;
  ledger_seq: number | null;
  decided_at: string | null;
};

export type ActionTypeInfo = {
  name: string;
  object_type: string;
  source_kind: string;
  key_fields: string[];
  field: string;
  value_min: string;
  value_max: string;
  description: string;
};

// --- refusals -------------------------------------------------------------------

export type RefusalDetail = {
  code?: string;
  object_key?: ObjectKey;
  candidates?: ObjectKey[];
  current_value?: string | null;
  current_row_version?: number;
  cortex_entry_id?: string;
  first_break?: number | string | null;
  reason?: string;
  [k: string]: unknown;
};

export type RefusalInfo = { code: string; detail?: RefusalDetail | null };

/**
 * One honest sentence per refusal. Mirrors the ACT-01/02 error map, the gate's
 * reasons and three client-side codes; tests/fixtures/actions/act02_refusal_codes.json
 * pins the set on both sides. None of them may say "Applied".
 */
export const REFUSAL_TEXT: Record<string, string> = {
  // spec validation (400)
  action_type_unknown: "That action type is not in the registry, so it was not proposed.",
  field_unknown: "The request named a field this action does not accept.",
  key_field_missing: "The object key is missing a required field (SKU and location).",
  key_field_extra: "The object key has a field this action does not take.",
  key_value_blank: "SKU and location cannot be blank.",
  key_value_invalid:
    "The SKU or location reads as personal data or is longer than 200 characters, so nothing was proposed.",
  op_unknown: "Unknown operation: only set and clear are allowed.",
  value_not_number: "The new value must be a number.",
  value_not_finite: "The new value must be a finite number.",
  value_out_of_range: "The new value is outside the range this action allows.",
  value_unchanged: "The new value equals the current one, so there is nothing to change.",
  reason_too_long: "The reason is too long.",
  approval_ref_required: "An approval reference of at least 3 characters is required.",
  approval_ref_too_long: "The approval reference is longer than 200 characters; use the ticket or change id.",
  // state (409)
  object_changed: "The value changed since this was proposed. Propose again from the current value.",
  token_diff_mismatch:
    "The stored proposal no longer matches its token, so it was refused rather than written.",
  lock_timeout: "Another change to this object is in progress. Nothing was written; try again.",
  stale_token: "This proposal is no longer pending: it was already decided or superseded.",
  action_requires_apply:
    "A governed action is applied from Governed actions with an approval reference, not confirmed.",
  conflict: "This proposal was already decided. Nothing was written twice.",
  decision_not_applied: "Only an applied decision can be reverted.",
  // apply (500 / 503)
  verify_failed: "The re-read did not match what was written, so the change was rolled back.",
  database_unavailable: "The control-plane database is not configured, so nothing was written.",
  ledger_unavailable:
    "The Cortex ledger is unreachable. Nothing was written, because there would be no receipt.",
  ledger_chain_broken: "The Cortex ledger chain did not verify, so nothing was written.",
  ledger_append_failed:
    "The ledger receipt could not be written, so the change was rolled back.",
  commit_failed_after_append:
    "A ledger entry was written but the change did not commit; a void entry was requested and nothing changed.",
  receipt_mismatch:
    "The receipt did not match what the database holds, so it was refused; a void entry was requested and nothing changed.",
  commit_outcome_unknown:
    "A ledger entry was written but the database could not confirm whether the change committed. Check Recent decisions before retrying.",
  ledger_entry_reused:
    "The ledger returned an entry that already backs another receipt; a void entry was requested and nothing changed.",
  // lookups (404 / 422)
  space_id_invalid: "The Space id is not valid.",
  space_not_found: "That Space does not exist in this tenant.",
  decision_not_found: "That decision was not found in this Space.",
  object_key_not_found: "No inventory item matches that SKU and location in the source.",
  object_key_ambiguous: "More than one source row matches that key. Nothing was proposed.",
  unsupported_source: "This action's source system is not supported, so nothing was proposed.",
  object_type_unknown: "That object type is not in the action registry.",
  evidence_unknown: "No governed answer has that audit id.",
  evidence_not_validated:
    "The cited answer is not a validated answer with executed SQL and rows, so it cannot back this change.",
  evidence_wrong_table: "The cited answer did not read the inventory table, so it cannot back this change.",
  evidence_wrong_space: "The cited answer was asked in another Space, so it cannot back a change in this one.",
  action_kind_reserved: "Action diffs can only be proposed through Governed actions.",
  actions_disabled: "Governed actions are turned off on this server.",
  // identity (403)
  steward_grant_required: "The configured identity does not hold the steward grant.",
  steward_identity_unset:
    "No steward identity is configured: the shipped default actor is refused.",
  steward_identity_invalid:
    "The configured steward identity is not a valid user id, so nothing was written.",
  actor_required: "No actor was named for this decision, so nothing was written.",
  // compliance gate (403, reason string)
  denied: "The compliance gate denied this action.",
  gate_denied: "The compliance gate denied this action.",
  gate_refused: "The compliance gate refused this action.",
  gate_task_unknown:
    "The compliance gate has no policy for this action yet (Cortex F5 holds no action.* policy), so it was refused.",
  gate_unavailable: "The compliance gate (Cortex) is unreachable, so the action was refused.",
  // client side
  not_found: "Governed actions are turned off on this server.",
  invalid_request: "The request was malformed, so nothing was sent to be written.",
  network_error: "The server could not be reached. Nothing is known to have changed.",
};

export const REFUSAL_CODES: readonly string[] = Object.freeze(Object.keys(REFUSAL_TEXT));

function keyText(key: ObjectKey | undefined): { sku: string; loc: string } | null {
  if (!key || typeof key.sku !== "string") return null;
  return { sku: key.sku, loc: typeof key.location_id === "string" ? key.location_id : "" };
}

function orList(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

export function describeRefusal(code: string, detail?: RefusalDetail | null): string {
  const c = (code || "").trim();
  if (!c) return "Refused: unknown";
  if (c === "object_key_not_found" && detail) {
    const asked = keyText(detail.object_key);
    if (asked) {
      const head = `No inventory item SKU "${asked.sku}"${asked.loc ? ` at ${asked.loc}` : ""}.`;
      const cands = (detail.candidates ?? [])
        .map((k) => keyText(k))
        .filter((k): k is { sku: string; loc: string } => k !== null)
        .map((k) => (k.loc && k.loc !== asked.loc ? `${k.sku} at ${k.loc}` : k.sku));
      return cands.length ? `${head} Did you mean ${orList(cands)}?` : head;
    }
  }
  if (c === "object_changed" && detail && "current_value" in detail) {
    return detail.current_value == null
      ? "The override was cleared since this was proposed."
      : `Value changed to ${detail.current_value} since this was proposed.`;
  }
  if (c === "commit_failed_after_append" && detail?.cortex_entry_id) {
    return `Ledger entry ${detail.cortex_entry_id} was written but the change did not commit; a void entry was requested and nothing changed.`;
  }
  if (c === "commit_outcome_unknown" && detail?.cortex_entry_id) {
    return `Ledger entry ${detail.cortex_entry_id} was written but the database could not confirm whether the change committed. Check Recent decisions before retrying.`;
  }
  return REFUSAL_TEXT[c] ?? `Refused: ${c}`;
}

export class ActionRefusal extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: RefusalDetail | null;
  readonly sentence: string;

  constructor(status: number, code: string, detail: RefusalDetail | null) {
    const sentence = describeRefusal(code, detail);
    super(sentence);
    this.name = "ActionRefusal";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.sentence = sentence;
  }

  get info(): RefusalInfo {
    return { code: this.code, detail: this.detail };
  }
}

/** Server error body -> (code, detail). FastAPI detail is a string, an object or a list. */
function refusalFrom(status: number, text: string): ActionRefusal {
  let detail: unknown = null;
  try {
    detail = (JSON.parse(text) as { detail?: unknown }).detail ?? null;
  } catch {
    return new ActionRefusal(status, `http_${status}`, null);
  }
  if (typeof detail === "string") {
    const code = detail === "Not Found" ? "not_found" : detail;
    return new ActionRefusal(status, code, null);
  }
  if (Array.isArray(detail)) return new ActionRefusal(status, "invalid_request", null);
  if (detail && typeof detail === "object") {
    const d = detail as RefusalDetail;
    return new ActionRefusal(status, String(d.code ?? `http_${status}`), d);
  }
  return new ActionRefusal(status, `http_${status}`, null);
}

/** Any thrown value -> RefusalInfo for rendering. */
export function refusalInfo(e: unknown): RefusalInfo {
  if (e instanceof ActionRefusal) return e.info;
  const msg = e instanceof Error ? e.message : String(e);
  return { code: msg || "unknown", detail: null };
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ActionRefusal(0, "network_error", null);
  }
  if (!res.ok) throw refusalFrom(res.status, await res.text());
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function q(spaceId: string): string {
  return `space_id=${encodeURIComponent(spaceId)}`;
}

// --- calls ----------------------------------------------------------------------

/** 'on' only on a 200 from the registry; any other status or a network error is 'off'. */
export async function probeActions(signal?: AbortSignal): Promise<ActionsAvailability> {
  try {
    const res = await fetch(`${BASE}/types`, { signal });
    return res.status === 200 ? "on" : "off";
  } catch {
    return "off";
  }
}

export function listActionTypes(): Promise<ActionTypeInfo[]> {
  return call<ActionTypeInfo[]>("/types");
}

export function listActionProposals(spaceId: string): Promise<ActionProposal[]> {
  return call<ActionProposal[]>(`/proposals?${q(spaceId)}`);
}

export type ProposeInput = {
  space_id: string;
  object_key: ObjectKey;
  value: string;
  reason?: string;
  evidence_audit_id?: string;
  action_type?: string;
};

export function proposeAction(input: ProposeInput): Promise<ProposeResult> {
  const body: Record<string, unknown> = {
    space_id: input.space_id,
    action_type: input.action_type ?? DEFAULT_ACTION_TYPE,
    object_key: input.object_key,
    value: input.value,
    reason: input.reason ?? "",
  };
  if (input.evidence_audit_id !== undefined) body.evidence_audit_id = input.evidence_audit_id;
  return post<ProposeResult>("/proposals", body);
}

export function applyAction(
  proposalId: string,
  idempotencyToken: string,
  approvalRef: string,
): Promise<ActionReceipt> {
  return post<ActionReceipt>(`/proposals/${encodeURIComponent(proposalId)}/apply`, {
    idempotency_token: idempotencyToken,
    approval_ref: approvalRef,
  });
}

export function declineAction(
  proposalId: string,
  idempotencyToken: string,
  approvalRef: string,
  reason = "",
): Promise<DeclineResult> {
  return post<DeclineResult>(`/proposals/${encodeURIComponent(proposalId)}/decline`, {
    idempotency_token: idempotencyToken,
    approval_ref: approvalRef,
    reason,
  });
}

/** Creates a NEW pending proposal that undoes a decision. It does not apply it. */
export function revertDecision(decisionId: string, reason = ""): Promise<ProposeResult> {
  return post<ProposeResult>(`/decisions/${encodeURIComponent(decisionId)}/revert`, { reason });
}

export function fetchOverrides(spaceId: string): Promise<ActionOverride[]> {
  return call<ActionOverride[]>(`/objects?object_type=inventory_item&${q(spaceId)}`);
}

export function fetchDecisions(spaceId: string): Promise<ActionDecision[]> {
  return call<ActionDecision[]>(`/decisions?${q(spaceId)}`);
}
