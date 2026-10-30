/**
 * INS-PROOF-03 - proven insights and steward pins (DMS_INSIGHT_PROOF, default off).
 *
 * Own fetchers and types, so the shared api.ts / types.ts stay untouched. The
 * browser talks only to DMS (/api/v1/insight-proof/*), never to Cortex.
 *
 * The card shows an insight only when its proof holds on the client too: the
 * grouped total conserves, the two Cortex executions share one result hash, and
 * every number in the headline is a cell of the rows shown. Otherwise the card
 * is the abstain card, with no rows. This client never computes a figure.
 */

import type { BadgeKind } from "@/lib/types";

export type ProofRow = {
  label: string | null;
  value: number;
  share_pct: number;
  cum_share_pct: number;
  grouped_total: number;
};

export type InsightEnvelope = {
  answer_id?: string;
  text: string;
  badge: BadgeKind;
  abstained: boolean;
  rows: ProofRow[];
  sql_used: string | null;
  audit_id?: string;
  assumptions?: string[];
};

export type ProofExecution = { engine: string; result_hash: string; row_count: number };

export type ProofDrift = {
  approved_result_hash: string;
  current_result_hash: string;
  labels_added: (string | null)[];
  labels_removed: (string | null)[];
  labels_changed: (string | null)[];
  reason: "source_refreshed" | "drift_without_source_change" | "source_change_unknown";
};

export type InsightProof = {
  insight_id?: string;
  asset_id?: string;
  kind?: string;
  verdict?: string;
  raw_total?: number;
  grouped_total?: number;
  tolerance?: number;
  conserves?: boolean;
  result_hash?: string | null;
  reexec_result_hash?: string | null;
  executions?: ProofExecution[];
  ledger?: { event_type: string; entry_id: string; seq: number | null } | null;
  definition?: {
    measure: string;
    expression: string;
    item_id: string | null;
    version_id: string | null;
    source?: string;
  };
  watermarks?: Record<string, string | null>;
  drift?: ProofDrift | null;
};

export type InsightItem = { envelope: InsightEnvelope; proof: InsightProof };

export type PinSummary = {
  asset_id: string;
  space_id: string | null;
  insight_id: string;
  measure: string;
  dimension: string;
  kind: string;
  /** Null when the server withholds it (its dimension is now PII, or unverifiable). */
  headline: string | null;
  headline_withheld?: string | null;
  expected_result_hash: string;
  approved_at: string;
  approved_by: string;
  ledger_entry_id: string;
};

export type PinPreview = InsightItem & { token: string; expires_at: string };

export type Refusal = { status: number; code: string; body: Record<string, unknown> };

const BASE = "/api/v1/insight-proof";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

async function call<T>(
  fetchImpl: FetchLike,
  path: string,
  init?: RequestInit,
): Promise<{ ok: true; body: T } | { ok: false; refusal: Refusal }> {
  const res = await fetchImpl(`${BASE}${path}`, init);
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (res.ok) return { ok: true, body: body as T };
  const obj = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const code =
    typeof obj.code === "string"
      ? obj.code
      : typeof obj.detail === "string"
        ? obj.detail
        : `http_${res.status}`;
  return { ok: false, refusal: { status: res.status, code, body: obj } };
}

function post(body?: unknown): RequestInit {
  return body === undefined
    ? { method: "POST" }
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
}

/** GET /status. A 404 means the flag is off and nothing is mounted. */
export async function fetchInsightProofStatus(
  fetchImpl: FetchLike = fetch,
): Promise<{ status: number; enabled: boolean }> {
  const res = await fetchImpl(`${BASE}/status`);
  if (!res.ok) return { status: res.status, enabled: false };
  const body = (await res.json()) as { enabled?: boolean };
  return { status: res.status, enabled: body.enabled === true };
}

export function runInsightProof(spaceId: string | null, fetchImpl: FetchLike = fetch) {
  return call<{ insights: InsightItem[]; refused: { candidate: string; code: string; text: string }[] }>(
    fetchImpl,
    "/run",
    post({ space_id: spaceId }),
  );
}

export function previewPin(spaceId: string | null, insightId: string, fetchImpl: FetchLike = fetch) {
  return call<PinPreview>(fetchImpl, "/pins/preview", post({ space_id: spaceId, insight_id: insightId }));
}

export function confirmPin(token: string, fetchImpl: FetchLike = fetch) {
  return call<{ asset_id: string; approved_at: string; ledger_entry_id: string }>(
    fetchImpl,
    "/pins/confirm",
    post({ token }),
  );
}

export function replayPin(assetId: string, fetchImpl: FetchLike = fetch) {
  return call<InsightItem & { verdict: string; asset_id: string }>(
    fetchImpl,
    `/pins/${encodeURIComponent(assetId)}/replay`,
    post(),
  );
}

export function revokePin(assetId: string, fetchImpl: FetchLike = fetch) {
  return call<{ asset_id: string }>(fetchImpl, `/pins/${encodeURIComponent(assetId)}/revoke`, post());
}

export function listPins(spaceId: string | null, fetchImpl: FetchLike = fetch) {
  const qs = spaceId ? `?space_id=${encodeURIComponent(spaceId)}` : "";
  return call<{ pins: PinSummary[] }>(fetchImpl, `/pins${qs}`);
}

// --------------------------------------------------------------------------
// proof lines and the card gate
// --------------------------------------------------------------------------

function money2(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function labelText(label: string | null): string {
  return label ?? "(no value)";
}

/** The "How this is proven" list. Every figure here is copied from the proof. */
export function proofLines(proof: InsightProof): string[] {
  const lines: string[] = [];
  if (typeof proof.grouped_total === "number" && typeof proof.raw_total === "number") {
    lines.push(
      `Grouped total ${money2(proof.grouped_total)} = raw total with no join ${money2(proof.raw_total)}` +
        ` (tolerance ${proof.tolerance ?? "unknown"})`,
    );
  }
  const execs = proof.executions ?? [];
  if (execs.length === 2 && execs[0].result_hash === execs[1].result_hash) {
    lines.push(`Ran twice through Cortex: same rows (result ${execs[0].result_hash.slice(0, 12)})`);
  }
  if (proof.ledger?.entry_id) {
    lines.push(`Ledger entry ${proof.ledger.entry_id}`);
  }
  const d = proof.definition;
  if (d) {
    lines.push(
      `Definition: ${d.measure} = ${d.expression} (item ${d.item_id ?? "none"} v${d.version_id ?? "none"})`,
    );
  }
  const marks = Object.values(proof.watermarks ?? {});
  const known = marks.length > 0 && marks.every((m) => typeof m === "string" && m.length > 0);
  const stamps = known ? Array.from(new Set(marks as string[])).join(", ") : "unknown";
  lines.push(`Source freshness: ${stamps}`);
  if (proof.drift) {
    lines.push(`Changed since approval: ${proof.drift.labels_changed.map(labelText).join(", ")}`);
  }
  return lines;
}

// scripts/brief.py _NUMBER: a numeric token not glued to a word, bare integers included.
const NUMBER = /(?<![A-Za-z_\d.,])\d[\d,]*(?:\.\d+)?(?![A-Za-z_\d])/g;
const REPLAY_PREFIX = /^Unchanged since steward approval on \d{4}-\d{2}-\d{2}: /;

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Throws when the headline states a number that is not a cell of ``rows``.
 * Bare integers up to the row count are the "k of n" counts the template states.
 * The replay prefix's approval date is the pin's own date, not a figure.
 */
export function assertNoNewFigures(headline: string, rows: ProofRow[]): void {
  let text = headline.replace(REPLAY_PREFIX, "");
  const labels = Array.from(new Set(rows.map((r) => r.label).filter((l): l is string => !!l))).sort(
    (a, b) => b.length - a.length,
  );
  for (const label of labels) {
    text = text.replace(
      new RegExp(`(?<![A-Za-z0-9_.,])${escapeRe(label)}(?![A-Za-z0-9_]|[.,]\\d)`, "g"),
      " ",
    );
  }
  const cells: number[] = [];
  for (const r of rows) {
    for (const v of [r.value, r.share_pct, r.cum_share_pct, r.grouped_total]) {
      if (typeof v === "number" && Number.isFinite(v)) cells.push(v);
    }
  }
  for (const token of text.match(NUMBER) ?? []) {
    const plain = token.replace(/,/g, "");
    const num = Number(plain);
    if (!plain.includes(".") && Number.isInteger(num) && num >= 1 && num <= rows.length) continue;
    if (cells.some((c) => Math.abs(c - num) < 1e-9)) continue;
    throw new Error(`headline figure ${token} is not a cell of the rows shown`);
  }
}

export type ProofCard =
  | {
      kind: "proven";
      headline: string;
      badge: BadgeKind;
      rows: ProofRow[];
      sql: string;
      lines: string[];
      insightId: string | null;
      assetId: string | null;
    }
  | { kind: "abstain"; text: string; reason: string };

export const ABSTAIN_CARD_TEXT =
  "This insight's proof did not hold here, so no figure is shown.";

/** The card to render, or the abstain card when any part of the proof does not hold. */
export function toProofCard(item: InsightItem): ProofCard {
  const { envelope: env, proof } = item;
  if (env.abstained || env.badge === "ABSTAIN") {
    return { kind: "abstain", text: env.text, reason: proof.verdict ?? "abstained" };
  }
  if (proof.conserves !== true) {
    return { kind: "abstain", text: ABSTAIN_CARD_TEXT, reason: "not_conserved" };
  }
  const execs = proof.executions ?? [];
  if (execs.length !== 2 || execs[0].result_hash !== execs[1].result_hash) {
    return { kind: "abstain", text: ABSTAIN_CARD_TEXT, reason: "result_changed_between_runs" };
  }
  try {
    assertNoNewFigures(env.text, env.rows);
  } catch {
    return { kind: "abstain", text: ABSTAIN_CARD_TEXT, reason: "headline_unbacked" };
  }
  return {
    kind: "proven",
    headline: env.text,
    badge: env.badge,
    rows: env.rows,
    sql: env.sql_used ?? "",
    lines: proofLines(proof),
    insightId: proof.insight_id ?? null,
    assetId: proof.asset_id ?? null,
  };
}

// --------------------------------------------------------------------------
// the steward approval: preview, then an explicit confirm
// --------------------------------------------------------------------------

export type ApprovalState =
  | { phase: "idle" }
  | { phase: "previewing" }
  | { phase: "confirming"; preview: PinPreview }
  | { phase: "pinning" }
  | { phase: "pinned"; assetId: string }
  | { phase: "refused"; code: string };

export type ApprovalApi = {
  preview: (spaceId: string | null, insightId: string) => ReturnType<typeof previewPin>;
  confirm: (token: string) => ReturnType<typeof confirmPin>;
};

/**
 * Nothing is pinned by clicking "Approve as steward": that only previews and
 * opens the confirm dialog. Confirm POSTs only from the dialog's own button.
 */
export function createApprovalFlow(
  api: ApprovalApi,
  spaceId: string | null,
  insightId: string,
  onChange: (s: ApprovalState) => void,
) {
  let state: ApprovalState = { phase: "idle" };
  const set = (s: ApprovalState) => {
    state = s;
    onChange(s);
  };
  return {
    get state() {
      return state;
    },
    async approve(): Promise<void> {
      if (state.phase !== "idle" && state.phase !== "refused") return;
      set({ phase: "previewing" });
      const got = await api.preview(spaceId, insightId);
      set(got.ok ? { phase: "confirming", preview: got.body } : { phase: "refused", code: got.refusal.code });
    },
    async confirm(): Promise<void> {
      if (state.phase !== "confirming") return;
      const token = state.preview.token;
      set({ phase: "pinning" });
      const got = await api.confirm(token);
      set(got.ok ? { phase: "pinned", assetId: got.body.asset_id } : { phase: "refused", code: got.refusal.code });
    },
    cancel(): void {
      if (state.phase === "confirming") set({ phase: "idle" });
    },
  };
}
