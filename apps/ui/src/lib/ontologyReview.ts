/**
 * ONTO-PROFILE-03 (dms#516): the steward review of a file Space's proposed
 * keys and links. The routes are ONTO-PROFILE-01's (dms#514).
 *
 * Two halves. The fetch calls talk to the DMS API only and never guess: a
 * status changes on screen only from the item the API returned. The pure
 * functions turn counts and reason codes into sentences. They receive counts,
 * table names, column names and landed types, and nothing else: the API sends
 * no cell value and this module adds none.
 *
 * Words: a profile item is "proposed", "approved" or "rejected". An approval is
 * a steward's statement, not a measurement, so it is never called anything
 * stronger here.
 */

// --- response shapes (dms#514) ---------------------------------------------------

export type ItemStatus = "proposed" | "approved" | "revoked";

export type KeyEvidence = {
  row_count?: number;
  distinct_count?: number;
  null_count?: number;
  column_types?: Record<string, string>;
  referenced_by?: unknown[];
  truncated?: boolean;
};

export type LinkEvidence = {
  child_rows?: number;
  child_null_rows?: number;
  child_distinct?: number;
  parent_distinct?: number;
  matched_parent_keys?: number;
  orphan_rows?: number;
  orphan_keys?: number;
  max_fanout?: number;
  child_is_own_key?: boolean;
  child_has_key?: boolean;
  column_types?: { child?: string; parent?: string };
  truncated?: boolean;
};

export type KeyPayload = { name?: string; relation: string; key: string[] };

export type LinkPayload = {
  name?: string;
  from_object: string;
  from_columns: string[];
  to_object: string;
  to_columns: string[];
  one_to_one?: boolean;
};

type ItemBase = {
  id: string;
  version_id?: string;
  status: ItemStatus | string;
  source?: string;
  approved_by?: string | null;
  ts?: string;
};

export type KeyItem = ItemBase & {
  kind: "object";
  payload: KeyPayload;
  provenance?: { profiler?: string; measured_at?: string; evidence?: KeyEvidence };
};

export type LinkItem = ItemBase & {
  kind: "link";
  payload: LinkPayload;
  provenance?: { profiler?: string; measured_at?: string; evidence?: LinkEvidence };
};

export type ProfileItem = KeyItem | LinkItem;

export type Observation = {
  child_table: string;
  child_columns: string[];
  parent_table: string;
  parent_columns: string[];
  evidence?: LinkEvidence;
  reason: string;
};

export type UnkeyedTable = {
  table: string;
  row_count?: number;
  reason: string;
  searched?: { single_columns?: number; pairs?: number };
};

export type MeasuredTable = {
  table: string;
  row_count?: number;
  columns?: { name: string; type: string }[];
  truncated?: boolean;
};

export type SkippedTable = { table: string; reason: string };

export type Bounds = Record<string, unknown>;

/** `POST /v1/ontology/data/profile` */
export type ProfileBody = {
  status: string;
  served: boolean;
  space_id: string;
  version_id?: string | null;
  items: ProfileItem[];
  not_remeasured?: string[];
  persisted?: boolean;
  hint?: string;
  tables?: MeasuredTable[];
  skipped?: SkippedTable[];
  observations?: Observation[];
  unkeyed_tables?: UnkeyedTable[];
  bounds?: Bounds;
};

/** `GET /v1/ontology/data/spaces/{space_id}/items` */
export type ItemsBody = {
  space_id: string;
  served: boolean;
  persisted?: boolean;
  items: ProfileItem[];
};

// --- refusals --------------------------------------------------------------------

export type Refusal =
  | { kind: "steward_grant_required" }
  | { kind: "no_file_tables"; skipped: SkippedTable[] }
  | { kind: "key_already_approved"; relation: string; approvedItemId: string | null }
  | { kind: "space_not_found" }
  | { kind: "item_not_found" }
  | { kind: "gate_refused"; reason: GateReason }
  | { kind: "error"; status: number };

/** Gate reasons this page has words for. Any other text from the API is not shown. */
export type GateReason = "gate_unavailable" | "gate_task_unknown" | "other";

export type Result<T> = { ok: true; body: T } | { ok: false; refusal: Refusal };

/** Name an API refusal from its status and body. Never decides an allowance. */
export function classifyRefusal(status: number, bodyText: string): Refusal {
  let detail: unknown = null;
  try {
    detail = (JSON.parse(bodyText) as { detail?: unknown }).detail ?? null;
  } catch {
    detail = null;
  }
  const code =
    typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string"
        ? (detail as { code: string }).code
        : "";
  const obj = (detail && typeof detail === "object" ? detail : {}) as Record<string, unknown>;
  if (status === 403 && code === "steward_grant_required") return { kind: "steward_grant_required" };
  if (status === 403) {
    const reason = code === "gate_unavailable" || code === "gate_task_unknown" ? code : "other";
    return { kind: "gate_refused", reason };
  }
  if (status === 409 && code === "no_file_tables") {
    const skipped = Array.isArray(obj.skipped) ? (obj.skipped as SkippedTable[]) : [];
    return { kind: "no_file_tables", skipped };
  }
  if (status === 409 && code === "key_already_approved") {
    return {
      kind: "key_already_approved",
      relation: typeof obj.relation === "string" ? obj.relation : "",
      approvedItemId: typeof obj.approved_item_id === "string" ? obj.approved_item_id : null,
    };
  }
  if (status === 404 && code === "space_not_found") return { kind: "space_not_found" };
  if (status === 404 && code === "ontology_item_not_found") return { kind: "item_not_found" };
  // The body's own text is never shown: it is not this page's wording and may quote data.
  return { kind: "error", status };
}

// --- fetch calls (DMS API only) ---------------------------------------------------

async function call<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, init);
  } catch (err) {
    if (init?.signal?.aborted) throw err;
    return {
      ok: false,
      refusal: { kind: "error", status: 0 },
    };
  }
  const text = await res.text();
  if (!res.ok) return { ok: false, refusal: classifyRefusal(res.status, text) };
  try {
    return { ok: true, body: JSON.parse(text) as T };
  } catch {
    return {
      ok: false,
      refusal: { kind: "error", status: res.status },
    };
  }
}

export function fetchSpaceItems(spaceId: string, signal?: AbortSignal): Promise<Result<ItemsBody>> {
  return call<ItemsBody>(`/v1/ontology/data/spaces/${encodeURIComponent(spaceId)}/items`, { signal });
}

export function measureSpace(spaceId: string): Promise<Result<ProfileBody>> {
  return call<ProfileBody>(`/v1/ontology/data/profile?space_id=${encodeURIComponent(spaceId)}`, {
    method: "POST",
  });
}

/** One item per call. There is no call here that takes more than one id. */
export function approveItem(itemId: string): Promise<Result<ProfileItem>> {
  return call<ProfileItem>(`/v1/ontology/data/items/${encodeURIComponent(itemId)}/approve`, {
    method: "POST",
  });
}

export function rejectItem(itemId: string): Promise<Result<ProfileItem>> {
  return call<ProfileItem>(`/v1/ontology/data/items/${encodeURIComponent(itemId)}/revoke`, {
    method: "POST",
  });
}

// --- words -----------------------------------------------------------------------

/** A missing count is said to be missing. It is never shown as 0. */
function n(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "not measured";
}

function rows(value: unknown): string {
  return value === 1 ? "1 row" : `${n(value)} rows`;
}

export function columnList(columns: readonly string[]): string {
  return columns.join(" + ");
}

/** Status word for a profile item. `revoked` is the API's word for a rejection. */
export function statusLabel(status: string): string {
  if (status === "proposed") return "proposed";
  if (status === "approved") return "approved";
  if (status === "revoked") return "rejected";
  // An unknown status is not echoed: the page has no words for it and claims nothing.
  return "status not recognised";
}

export const APPROVED_NOTE = "Approved by a steward. It is re-measured at every question.";

export const MEMORY_NOTICE_TITLE = "Proposals and approvals are kept in memory";
export const MEMORY_NOTICE_HINT =
  "They are lost when the DMS API restarts. Nothing on this page survives a restart until the item store is on Postgres.";

export const LINK_NOT_USABLE = "not usable until both keys are approved";

/** "300 rows, 300 distinct, 0 empty" */
export function keyEvidenceSentence(evidence: KeyEvidence | undefined): string {
  const e = evidence ?? {};
  const base = `${rows(e.row_count)}, ${n(e.distinct_count)} distinct, ${n(e.null_count)} empty`;
  return e.truncated ? `${base}. Measured on a truncated table` : base;
}

/** rows, distinct values, matched parent keys out of parent keys, largest fan-out */
export function linkEvidenceSentence(evidence: LinkEvidence | undefined): string {
  const e = evidence ?? {};
  const parts = [
    rows(e.child_rows),
    `${n(e.child_distinct)} distinct values`,
    `${n(e.matched_parent_keys)} of ${n(e.parent_distinct)} parent keys matched`,
    `largest fan-out ${n(e.max_fanout)}`,
    `${n(e.child_null_rows)} empty`,
  ];
  const base = parts.join(", ");
  return e.truncated ? `${base}. Measured on a truncated table` : base;
}

/** Why a searched link was not proposed, in plain words. */
export function observationSentence(obs: Observation): string {
  const e = obs.evidence ?? {};
  const child = `${obs.child_table}.${columnList(obs.child_columns)}`;
  const parent = `${obs.parent_table}.${columnList(obs.parent_columns)}`;
  if (obs.reason === "orphans") {
    return (
      `${rows(e.orphan_rows)} of ${child} point at a ${parent} that does not exist ` +
      `(${n(e.orphan_keys)} distinct values with no match), so no link is proposed.`
    );
  }
  if (obs.reason === "child_has_no_key") {
    return (
      `Every ${child} exists in ${parent}, but ${obs.child_table} has no column that ` +
      "identifies a row, so no link is proposed."
    );
  }
  return `${child} to ${parent} was not proposed (reason code: ${safeCode(obs.reason)}).`;
}

export function unkeyedSentence(table: UnkeyedTable): string {
  const s = table.searched ?? {};
  if (table.reason === "no_unique_column_or_pair") {
    return (
      `This table has no column that identifies a row. ${n(s.single_columns)} columns and ` +
      `${n(s.pairs)} pairs of columns were checked across ${rows(table.row_count)}.`
    );
  }
  if (table.reason === "empty_table") return "This table has no rows, so no key can be measured.";
  return `No key was found (reason code: ${safeCode(table.reason)}).`;
}

export function skippedSentence(row: SkippedTable): string {
  if (row.reason === "sql_source") {
    return `${row.table} comes from a SQL source, so it is not measured here.`;
  }
  return `${row.table} was not measured (reason code: ${safeCode(row.reason)}).`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function names(value: unknown): string {
  return Array.isArray(value) && value.length ? value.map(String).join(", ") : "none";
}

/**
 * A reason code or key name from the API, shown only when it is a plain
 * identifier. Anything else is free text this page did not write, so it is
 * replaced: the page never echoes it.
 */
export function safeCode(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  return /^[a-z0-9_]{1,48}$/.test(text) && !/verif|certif/.test(text) ? text : "not recognised";
}

/**
 * A bound this module has no sentence for is still listed, never dropped: its
 * name, and its value when that is a number or a yes/no. Free text is not echoed.
 */
function asStated(key: string, value: unknown): string {
  const name = key
    .split(" ")
    .map((part) => (/^[A-Za-z0-9_.]{1,64}$/.test(part) && !/verif|certif/i.test(part) ? part : "not recognised"))
    .join(" ");
  if (typeof value === "number" || typeof value === "boolean") return `${name}: ${String(value)}`;
  return `${name}: stated by the API, and this page has no words for it yet`;
}

const LINK_COUNT_WORDS: Record<string, string> = {
  pairs_measured: "column pairs measured",
  pairs_type_incompatible: "column pairs left out because the landed types differ",
  pairs_child_all_null: "column pairs left out because the child column is empty",
  pairs_below_orphan_threshold: "column pairs left out because too few values matched",
  pairs_over_cap_not_measured: "column pairs over the limit and not measured",
  pair_keys_not_searched_as_parents: "two-column keys not searched as a parent",
};

/**
 * Every bound the profiler stated, one sentence each. A key this function does
 * not know is still listed, as stated, so a new bound cannot go missing here.
 */
export function boundSentences(bounds: Bounds | undefined): string[] {
  if (!bounds) return [];
  const out: string[] = [];
  for (const [key, value] of Object.entries(bounds)) {
    if (key === "max_columns_per_table") {
      out.push(`At most ${n(value)} columns per table are measured.`);
    } else if (key === "columns_not_measured" && isRecord(value)) {
      const tables = Object.entries(value);
      if (!tables.length) out.push("No column was left unmeasured by the column limit.");
      for (const [table, cols] of tables) {
        out.push(`${table}: columns over the limit and not measured: ${names(cols)}.`);
      }
    } else if (key === "pair_search" && isRecord(value)) {
      for (const [k, v] of Object.entries(value)) {
        if (k === "when") out.push(`Two-column keys are searched ${String(v)}.`);
        else if (k === "max_columns") {
          out.push(`At most ${n(v)} columns per table are paired.`);
        } else if (k === "wider_than_pairs") {
          out.push(`Keys wider than two columns: ${String(v)}.`);
        } else if (k === "tables" && isRecord(v)) {
          for (const [table, t] of Object.entries(v)) {
            const row = isRecord(t) ? t : {};
            out.push(
              `${table}: ${n(row.pairs_searched)} pairs searched. Columns with empty values, ` +
                `not paired: ${names(row.columns_with_nulls_not_paired)}. Columns over the pair ` +
                `limit, not paired: ${names(row.columns_over_pair_cap_not_paired)}.`,
            );
            for (const [extra, ev] of Object.entries(row)) {
              if (
                extra !== "pairs_searched" &&
                extra !== "columns_with_nulls_not_paired" &&
                extra !== "columns_over_pair_cap_not_paired"
              ) {
                out.push(asStated(`${table} ${extra}`, ev));
              }
            }
          }
        } else out.push(asStated(`pair search ${k}`, v));
      }
    } else if (key === "link_search" && isRecord(value)) {
      for (const [k, v] of Object.entries(value)) {
        if (k === "shape") out.push(`Links searched: ${String(v)}.`);
        else if (k === "type_rule") out.push(`Type rule: ${String(v)}.`);
        else if (k === "self_references") {
          out.push(`Links from a table to itself: ${String(v)}.`);
        } else if (k === "max_pairs") {
          out.push(`At most ${n(v)} column pairs are measured for links.`);
        } else if (k in LINK_COUNT_WORDS) out.push(`${n(v)} ${LINK_COUNT_WORDS[k]}.`);
        else out.push(asStated(`link search ${k}`, v));
      }
    } else if (key === "orphan_observation_min_match_ratio") {
      out.push(
        `A link with unmatched rows is listed as an observation only when at least ` +
          `${n(typeof value === "number" ? Math.round(value * 100) : value)}% of its distinct values match.`,
      );
    } else if (key === "truncated_tables") {
      out.push(`Tables measured on truncated data: ${names(value)}.`);
    } else if (key === "unmeasured" && Array.isArray(value)) {
      if (!value.length) out.push("No measurement failed.");
      for (const row of value) {
        const r = isRecord(row) ? row : {};
        const what = r.child_table
          ? `${String(r.child_table)}.${names(r.child_columns)} to ${String(r.parent_table)}.${names(r.parent_columns)}`
          : String(r.table ?? "a table");
        const cls = typeof r.error === "string" && /^[A-Za-z]{1,48}$/.test(r.error) ? r.error : "not recognised";
        out.push(`Could not be measured: ${what} (error class: ${cls}).`);
      }
    } else out.push(asStated(key, value));
  }
  return out;
}

// --- view model ------------------------------------------------------------------

export function isKey(item: ProfileItem): item is KeyItem {
  return item.kind === "object";
}

export function isLink(item: ProfileItem): item is LinkItem {
  return item.kind === "link";
}

function sameColumns(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((c, i) => c === b[i]);
}

export type LinkUsability = { usable: boolean; childKeyApproved: boolean; parentKeyApproved: boolean };

/**
 * A link can be used only when the child table has an approved key and the
 * parent columns are the parent table's approved key. Reads statuses only.
 */
export function linkUsability(link: LinkItem, items: readonly ProfileItem[]): LinkUsability {
  const approvedKeys = items.filter(isKey).filter((i) => i.status === "approved");
  const childKeyApproved = approvedKeys.some((i) => i.payload.relation === link.payload.from_object);
  const parentKeyApproved = approvedKeys.some(
    (i) =>
      i.payload.relation === link.payload.to_object &&
      sameColumns(i.payload.key, link.payload.to_columns),
  );
  return { usable: childKeyApproved && parentKeyApproved, childKeyApproved, parentKeyApproved };
}

/**
 * What stands between this link and an answer, or null when nothing does: the
 * link is approved and so are both keys. A rejected link is said to be rejected
 * whatever its keys are.
 */
export function linkUsabilitySentence(link: LinkItem, items: readonly ProfileItem[]): string | null {
  if (link.status === "revoked") return "Rejected: this link is not used, whatever its keys are.";
  const u = linkUsability(link, items);
  if (!u.usable) {
    const missing: string[] = [];
    if (!u.childKeyApproved) missing.push(`a key of ${link.payload.from_object}`);
    if (!u.parentKeyApproved) {
      missing.push(`${columnList(link.payload.to_columns)} as the key of ${link.payload.to_object}`);
    }
    return `${LINK_NOT_USABLE}. Not approved yet: ${missing.join(" and ")}.`;
  }
  if (link.status !== "approved") {
    return "Both keys are approved. This link itself is not approved yet, so it is not used.";
  }
  return null;
}

/** 409 `key_already_approved`: say which key holds the table. */
export function keyConflictSentence(
  refusal: Extract<Refusal, { kind: "key_already_approved" }>,
  items: readonly ProfileItem[],
): string {
  const holder =
    items.filter(isKey).find((i) => i.id === refusal.approvedItemId) ??
    items
      .filter(isKey)
      .find((i) => i.status === "approved" && i.payload.relation === refusal.relation);
  const table = refusal.relation || holder?.payload.relation || "this table";
  if (!holder) {
    return `Another key is already approved for ${table}. A table has one approved key. This item stays proposed.`;
  }
  return (
    `${columnList(holder.payload.key)} is already approved as the key of ${table}. ` +
    "A table has one approved key: reject that one first. This item stays proposed."
  );
}

export function refusalSentence(refusal: Refusal): string {
  switch (refusal.kind) {
    case "steward_grant_required":
      return "A steward grant is needed to review keys and links. Ask an admin for the steward role.";
    case "no_file_tables":
      return (
        "This Space has no table landed from a file, so there is nothing to measure. " +
        "A SQL-source Space is not measured: its keys and links come from the database's own " +
        "declared keys and are shown on that Space's row on the Spaces page."
      );
    case "key_already_approved":
      return `Another key is already approved for ${refusal.relation || "this table"}.`;
    case "space_not_found":
      return "This Space does not exist on the DMS API. It may have been lost on a restart.";
    case "item_not_found":
      return "This item no longer exists on the DMS API. Measure the Space again.";
    case "gate_refused":
      if (refusal.reason === "gate_unavailable") {
        return "The Cortex gate is unreachable, so the request was refused. Nothing was changed.";
      }
      if (refusal.reason === "gate_task_unknown") {
        return "Cortex does not know this task yet, so the request was refused. Nothing was changed.";
      }
      return "The compliance gate refused this request. Nothing was changed.";
    case "error":
      return refusal.status === 0
        ? "The DMS API could not be reached. Nothing was changed on this page."
        : `The request failed (the API answered ${refusal.status}). Nothing was changed on this page.`;
  }
}

export type TableReview = {
  table: string;
  rowCount: number | null;
  columnCount: number | null;
  keys: KeyItem[];
  links: LinkItem[];
  observations: Observation[];
  unkeyed: UnkeyedTable | null;
};

/**
 * Group everything by the table it belongs to (a link and an observation
 * belong to their child table). Tables keep the order the API gave.
 */
export function tableReviews(
  items: readonly ProfileItem[],
  measured?: Pick<ProfileBody, "tables" | "observations" | "unkeyed_tables"> | null,
): TableReview[] {
  const byTable = new Map<string, TableReview>();
  const get = (table: string): TableReview => {
    let row = byTable.get(table);
    if (!row) {
      row = {
        table,
        rowCount: null,
        columnCount: null,
        keys: [],
        links: [],
        observations: [],
        unkeyed: null,
      };
      byTable.set(table, row);
    }
    return row;
  };
  for (const t of measured?.tables ?? []) {
    const row = get(t.table);
    row.rowCount = typeof t.row_count === "number" ? t.row_count : null;
    row.columnCount = Array.isArray(t.columns) ? t.columns.length : null;
  }
  for (const item of items) {
    if (isKey(item)) get(item.payload.relation).keys.push(item);
    else if (isLink(item)) get(item.payload.from_object).links.push(item);
  }
  for (const obs of measured?.observations ?? []) get(obs.child_table).observations.push(obs);
  for (const t of measured?.unkeyed_tables ?? []) get(t.table).unkeyed = t;
  return [...byTable.values()];
}

const KNOWN_STATUSES: readonly string[] = ["proposed", "approved", "revoked"];

/** True when the API's answer to a press is that same item with a status this page knows. */
export function isAnswerFor(item: ProfileItem, answer: unknown): answer is ProfileItem {
  const a = answer as { id?: unknown; status?: unknown; kind?: unknown } | null;
  return (
    !!a &&
    a.id === item.id &&
    a.kind === item.kind &&
    typeof a.status === "string" &&
    KNOWN_STATUSES.includes(a.status)
  );
}

/** Replace one item with the row the API returned. Nothing else changes. */
export function withItem(items: readonly ProfileItem[], updated: ProfileItem): ProfileItem[] {
  return items.map((item) => (item.id === updated.id ? updated : item));
}

// --- the review of one Space, as a plain object -----------------------------------

export type ReviewState = {
  spaceId: string;
  loading: boolean;
  measuring: boolean;
  items: ProfileItem[];
  /** The last measurement of this session. Not stored by the API. */
  measured: ProfileBody | null;
  persisted: boolean | null;
  /** A refusal for the whole page (load or measure). */
  refusal: Refusal | null;
  /** Item id -> the press that is waiting for the API. */
  pending: Record<string, "approve" | "reject">;
  /** Item id -> what the API said when it refused the last press. */
  itemNotice: Record<string, string>;
  /** Stored items whose table or column the last measurement no longer found. */
  stale: "all" | string[];
};

export type ReviewApi = {
  fetchSpaceItems: typeof fetchSpaceItems;
  measureSpace: typeof measureSpace;
  approveItem: typeof approveItem;
  rejectItem: typeof rejectItem;
};

export function hasNoGrant(state: ReviewState): boolean {
  return state.refusal?.kind === "steward_grant_required";
}

export function isStale(state: ReviewState, item: ProfileItem): boolean {
  return state.stale === "all" || state.stale.includes(item.id);
}

/** Approve is offered only for an item the Space still has, and never without a grant. */
export function canApprove(state: ReviewState, item: ProfileItem): boolean {
  return !hasNoGrant(state) && item.status !== "approved" && !isStale(state, item);
}

export function canReject(state: ReviewState, item: ProfileItem): boolean {
  return !hasNoGrant(state) && item.status !== "revoked";
}

/** No press while a measurement is in flight, and no second press on one item. */
export function pressBlocked(state: ReviewState, item: ProfileItem): boolean {
  return state.loading || state.measuring || !!state.pending[item.id];
}

/** No measurement while a press is in flight: its answer could be older than the press's. */
export function measureBlocked(state: ReviewState): boolean {
  return state.loading || state.measuring || Object.keys(state.pending).length > 0;
}

export function showMemoryNotice(state: ReviewState): boolean {
  return state.persisted === false;
}

export type Review = {
  getState: () => ReviewState;
  subscribe: (listener: () => void) => () => void;
  load: () => Promise<void>;
  measure: () => Promise<void>;
  press: (item: ProfileItem, action: "approve" | "reject") => Promise<void>;
  dispose: () => void;
};

/**
 * The review of one Space. State is held here, not in render state, so two
 * presses in one tick see each other, and every rule is testable without a
 * browser. Three rules:
 * - a status changes only from the item the API returned for that press;
 * - a measurement and a press never overlap, so the page cannot show a status
 *   older than one the API already confirmed;
 * - after `dispose` (the page moved to another Space) a late answer changes nothing.
 */
export function createReview(
  spaceId: string,
  api: ReviewApi = { fetchSpaceItems, measureSpace, approveItem, rejectItem },
): Review {
  let state: ReviewState = {
    spaceId,
    loading: true,
    measuring: false,
    items: [],
    measured: null,
    persisted: null,
    refusal: null,
    pending: {},
    itemNotice: {},
    stale: [],
  };
  let disposed = false;
  const listeners = new Set<() => void>();
  const set = (patch: Partial<ReviewState>) => {
    if (disposed) return;
    state = { ...state, ...patch };
    for (const listener of listeners) listener();
  };
  const without = <T>(map: Record<string, T>, id: string): Record<string, T> => {
    const { [id]: _gone, ...rest } = map;
    return rest;
  };

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    dispose() {
      disposed = true;
      listeners.clear();
    },
    async load() {
      const result = await api.fetchSpaceItems(spaceId);
      if (result.ok) {
        set({
          loading: false,
          items: result.body.items ?? [],
          persisted: typeof result.body.persisted === "boolean" ? result.body.persisted : null,
        });
      } else {
        set({ loading: false, refusal: result.refusal });
      }
    },
    async measure() {
      if (disposed || hasNoGrant(state) || measureBlocked(state)) return;
      set({ measuring: true, refusal: null });
      const result = await api.measureSpace(spaceId);
      if (!result.ok) {
        set({
          measuring: false,
          refusal: result.refusal,
          // No file table left: every stored proposal is for a table the Space no longer has.
          ...(result.refusal.kind === "no_file_tables" ? { stale: "all" as const, measured: null } : {}),
        });
        return;
      }
      const body = result.body;
      const kept = new Set(body.not_remeasured ?? []);
      const fresh = new Set(body.items.map((i) => i.id));
      set({
        measuring: false,
        items: [...body.items, ...state.items.filter((i) => kept.has(i.id) && !fresh.has(i.id))],
        measured: body,
        persisted: typeof body.persisted === "boolean" ? body.persisted : null,
        itemNotice: {},
        stale: [...kept],
      });
    },
    async press(item, action) {
      if (disposed || pressBlocked(state, item)) return;
      if (!(action === "approve" ? canApprove(state, item) : canReject(state, item))) return;
      set({ pending: { ...state.pending, [item.id]: action }, itemNotice: without(state.itemNotice, item.id) });
      const result = await (action === "approve" ? api.approveItem(item.id) : api.rejectItem(item.id));
      const pending = without(state.pending, item.id);
      if (result.ok) {
        if (isAnswerFor(item, result.body)) {
          set({ pending, items: withItem(state.items, result.body) });
        } else {
          set({
            pending,
            itemNotice: {
              ...state.itemNotice,
              [item.id]:
                "The API answer was not this item with a status this page knows. The status shown is unchanged: reload to read it again.",
            },
          });
        }
        return;
      }
      if (result.refusal.kind === "steward_grant_required") {
        set({ pending, refusal: result.refusal });
        return;
      }
      const sentence =
        result.refusal.kind === "key_already_approved"
          ? keyConflictSentence(result.refusal, state.items)
          : refusalSentence(result.refusal);
      set({ pending, itemNotice: { ...state.itemNotice, [item.id]: sentence } });
    },
  };
}
