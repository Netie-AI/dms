/** DMS API client — browser talks only to DMS (proxied /api). Never Cortex. */

import { ontologyBundle, ontologyReadPath, type OntologyReadBody } from "./ontologyRead";
import type {
  AdminOverview,
  AnswerEnvelope,
  LibrarySource,
  OntologyBundle,
  RunsBody,
  SpaceSummary,
  TrustRunDetailItem,
  TrustSummary,
} from "./types";

/** CCA-07: the stage trace shape the answer envelope carries (CCA-01 schema).
 *  Re-exported here so envelope consumers get it from the API surface. */
export type { ConstraintStage } from "./constraintTrace";

export type HealthBody = {
  status: string;
  product?: string;
  contract?: string;
  ask_mode?: "demo" | "live";
  demo_fallback?: boolean;
  database_configured?: boolean;
  backend?: string;
  database?: {
    backend?: string;
    persistent?: boolean;
    configured?: boolean;
    url_set?: boolean;
    hint?: string | null;
  };
  dependencies?: {
    cortex?: {
      ok?: boolean;
      url?: string;
      error?: string;
      contract_routes?: boolean;
      contract_probe?: { hint?: string | null; status_code?: number };
      jwks_refresh?: {
        ok?: boolean;
        refresh_ok?: boolean;
        hint?: string | null;
        key_count?: number | null;
      };
    };
    openvault?: {
      ok?: boolean;
      url?: string;
      error?: string;
      root_hint?: string | null;
      start_hint?: string | null;
      trust?: {
        ok?: boolean;
        jwks_ok?: boolean;
        key_count?: number;
        hint?: string | null;
      };
    };
  };
};

function detailReason(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.flatMap((item) => {
      if (item && typeof item === "object" && typeof (item as { msg?: unknown }).msg === "string") {
        return [(item as { msg: string }).msg];
      }
      return [];
    });
    return msgs.length ? msgs.join("; ") : null;
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; code?: unknown };
    if (typeof d.message === "string" && d.message.trim()) {
      return typeof d.code === "string" ? `${d.code}: ${d.message}` : d.message;
    }
  }
  return null;
}

/** Strip a just-submitted secret from copy that might otherwise echo it (R-0004). */
export function redactSecret(text: string, secret?: string): string {
  if (!secret) return text;
  return text.split(secret).join("[redacted]");
}

const SERVICE_DOWN = "The analysis service is not reachable, so this step did not run.";

function looksLikeTransport(text: string): boolean {
  return (
    text.includes("ConnectError") ||
    text.includes("Errno") ||
    text.trim().startsWith("{") ||
    text.trim().startsWith("[")
  );
}

/** Turn a raw API error body into a sentence a steward can act on. */
export function describeApiError(body: string, secret?: string): string {
  let reason = body.trim();
  let code = "";
  try {
    const parsed = JSON.parse(body) as {
      detail?: unknown;
      message?: unknown;
      code?: unknown;
    };
    if (typeof parsed.code === "string") code = parsed.code;
    const extracted = detailReason(parsed.detail);
    if (extracted) reason = extracted;
    else if (typeof parsed.message === "string" && parsed.message.trim()) {
      reason = parsed.message.trim();
    }
  } catch {
    /* keep raw body */
  }
  if (code === "cortex_unavailable" || reason === "cortex_unavailable" || looksLikeTransport(reason)) {
    return SERVICE_DOWN;
  }
  if (reason === "gate_unavailable") {
    return "Cortex gate is unreachable. Start Cortex before writing -- an ungated change is refused.";
  }
  if (reason === "gate_task_unknown") {
    return "Cortex does not know this task yet. The write is refused rather than applied ungated.";
  }
  const cleaned = redactSecret(reason, secret);
  return cleaned || "The write was refused. No error detail was returned.";
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthBody | null> {
  try {
    const res = await fetch("/api/health", { signal });
    if (!res.ok) return null;
    return (await res.json()) as HealthBody;
  } catch (err) {
    // Abort is not "API down" — React StrictMode unmounts the first effect and
    // treating that as offline painted a red banner over a live stack.
    if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) {
      throw err;
    }
    return null;
  }
}

export type SpacesListBody = {
  spaces: SpaceSummary[];
  persisted: boolean;
  storage?: { backend?: string; persistent?: boolean; configured?: boolean };
  hint?: string | null;
};

export async function fetchSpaces(signal?: AbortSignal): Promise<SpacesListBody> {
  const res = await fetch("/api/v1/spaces", { signal });
  if (!res.ok) throw new Error(`spaces ${res.status}`);
  return (await res.json()) as SpacesListBody;
}

export type AskPayload = {
  question: string;
  space_id?: string | null;
  session_id?: string | null;
  /** Tables the user grounded the question in. Narrows the session manifest,
   *  so the scope is enforced by the engine, not suggested to the model. */
  grounded_tables?: string[] | null;
  /** Set only when the user picked a clarify reading. Omitted otherwise. */
  clarify_id?: string | null;
  reading_id?: string | null;
};

/** Ask JSON. clarify_id and reading_id are omitted unless both were picked,
 *  so a normal ask stays the same bytes. */
export function askRequestBody(payload: AskPayload): Record<string, unknown> {
  const body: Record<string, unknown> = {
    question: payload.question,
    space_id: payload.space_id ?? undefined,
    session_id: payload.session_id ?? undefined,
    grounded_tables: payload.grounded_tables?.length ? payload.grounded_tables : undefined,
  };
  const clarifyId = payload.clarify_id?.trim() ?? "";
  const readingId = payload.reading_id?.trim() ?? "";
  if (clarifyId && readingId) {
    body.clarify_id = clarifyId;
    body.reading_id = readingId;
  }
  return body;
}

export async function postAsk(
  payload: AskPayload,
  signal?: AbortSignal,
  askId?: string,
): Promise<AnswerEnvelope> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (askId) headers["X-Dms-Ask-Id"] = askId;
  const res = await fetch("/api/v1/chat/ask", {
    method: "POST",
    headers,
    body: JSON.stringify(askRequestBody(payload)),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`ask ${res.status}: ${detail}`);
  }
  return (await res.json()) as AnswerEnvelope;
}

export async function postAskConfirm(
  body: { token: string; yes: boolean },
  signal?: AbortSignal,
): Promise<AnswerEnvelope> {
  const res = await fetch("/api/v1/chat/ask/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: body.token, yes: body.yes ? "yes" : "no" }),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`confirm ${res.status}: ${detail}`);
  }
  return (await res.json()) as AnswerEnvelope;
}

/** One tap on a clarify reading. The server runs that reading_id. */
export function postClarifyPick(
  body: {
    question: string;
    space_id?: string | null;
    session_id?: string | null;
    clarify_id: string;
    reading_id: string;
  },
  signal?: AbortSignal,
  askId?: string,
): Promise<AnswerEnvelope> {
  return postAsk(
    {
      question: body.question,
      space_id: body.space_id,
      session_id: body.session_id,
      clarify_id: body.clarify_id,
      reading_id: body.reading_id,
    },
    signal,
    askId,
  );
}

export type AccessRequestResult = { ok: true } | { ok: false; missing: boolean };

/** POST /v1/spaces/{id}/access-requests. 404/405/501 means the route is not there. */
export async function postAccessRequest(
  spaceId: string,
  body: { table: string },
  signal?: AbortSignal,
): Promise<AccessRequestResult> {
  const id = spaceId.trim();
  const table = body.table.trim();
  if (!id || !table) return { ok: false, missing: false };
  const res = await fetch(`/api/v1/spaces/${encodeURIComponent(id)}/access-requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ table }),
    signal,
  });
  if (res.status === 404 || res.status === 405 || res.status === 501) {
    return { ok: false, missing: true };
  }
  if (!res.ok) return { ok: false, missing: false };
  return { ok: true };
}

/** What the access card does after the POST. A missing route hides the button. */
export function nextAccessRequestState(result: AccessRequestResult): {
  routeMissing: boolean;
  note: string | null;
} {
  if (!result.ok && result.missing) return { routeMissing: true, note: null };
  if (result.ok) return { routeMissing: false, note: "Request sent." };
  return { routeMissing: false, note: "The access request was refused." };
}

/* ── Generic JSON read ─────────────────────────────────────────────────────
 * Pages that read reference data render their own error state, so a failed
 * fetch throws with a message worth printing rather than resolving to null.
 */
async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api${path}`, { signal });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return (await res.json()) as T;
}

/* ── Ontology ───────────────────────────────────────────────────────────── */

/** One GET. Counts and the lists are on that body, so a slow section cannot hold the tiles. */
export async function fetchOntology(
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<OntologyBundle> {
  const body = await getJson<OntologyReadBody>(ontologyReadPath("", spaceId), signal);
  return ontologyBundle(body);
}

/* ── Trust ──────────────────────────────────────────────────────────────── */

export function fetchTrustSummary(signal?: AbortSignal): Promise<TrustSummary> {
  return getJson<TrustSummary>("/v1/trust/summary", signal);
}

export function fetchTrustRun(
  name: string,
  outcome?: string,
  signal?: AbortSignal,
): Promise<{ ok: boolean; id: string; label?: string; items: TrustRunDetailItem[]; hint?: string }> {
  const qs = outcome ? `?outcome=${encodeURIComponent(outcome)}` : "";
  return getJson(`/v1/trust/runs/${encodeURIComponent(name)}${qs}`, signal);
}

export type SpaceBenchTally = {
  n: number;
  graded: number;
  coverage: number | null;
  correct: number;
  WRONG: number;
  clarify: number;
  reconfirm: number;
  abstain: number;
  refusal_wrong: number;
  invalid_gold: number;
};

export type SpaceBenchResult = {
  id: string;
  question: string;
  served_sql: string;
  outcome: string;
  reason: string;
  gold_n: number;
  served_n: number;
  review: string | null;
  review_note: string;
};

export type SpaceBenchRun = {
  id: string;
  status: string;
  progress_done: number;
  progress_total: number;
  tally: SpaceBenchTally;
  results: SpaceBenchResult[];
};

export type SpaceBenchItem = {
  id: string;
  question: string;
  gold_sql: string;
  status: string;
  invalid_reason: string;
  source: string;
};

export type SpaceBenchBody = {
  enabled: boolean;
  space_id: string;
  items: SpaceBenchItem[];
  latest_run: SpaceBenchRun | null;
};

export function spaceBenchPath(spaceId: string): string {
  return `/v1/spaces/${encodeURIComponent(spaceId)}/benchmarks`;
}

export function fetchSpaceBench(spaceId: string, signal?: AbortSignal): Promise<SpaceBenchBody> {
  return getJson<SpaceBenchBody>(spaceBenchPath(spaceId), signal);
}

export async function postSpaceBench(
  path: string,
  body?: unknown,
): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? "{}" : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = String(res.status);
    try {
      const payload = (await res.json()) as { detail?: unknown };
      if (typeof payload.detail === "string" && payload.detail) detail = payload.detail;
    } catch {
      detail = String(res.status);
    }
    throw new Error(detail);
  }
}

/* ── Runs · Admin · Library · Spaces ────────────────────────────────────── */

/** Path for the runs feed. When the UI has an active Space, space_id must travel —
 *  the API already scopes; omitting it is the leftover that showed every Space. */
export function runsPath(kind?: string, spaceId?: string | null): string {
  const qs = new URLSearchParams();
  if (kind) qs.set("kind", kind);
  if (spaceId) qs.set("space_id", spaceId);
  const q = qs.toString();
  return `/v1/runs${q ? `?${q}` : ""}`;
}

export function fetchRuns(
  kind?: string,
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<RunsBody> {
  return getJson<RunsBody>(runsPath(kind, spaceId), signal);
}

/** Amend proposal list — same rule as runs: active Space → space_id on the wire. */
export function amendProposalsPath(spaceId?: string | null): string {
  const qs = spaceId ? `?space_id=${encodeURIComponent(spaceId)}` : "";
  return `/v1/amend/proposals${qs}`;
}

export type AmendProposal = {
  id: string;
  space_id?: string | null;
  created_at?: string | null;
  version_num?: number;
  status?: string;
  idempotency_token?: string;
  diff?: { summary?: string; plain?: string };
};

export function fetchAmendProposals(
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<AmendProposal[]> {
  return getJson<AmendProposal[]>(amendProposalsPath(spaceId), signal);
}

export async function postAmendProposal(
  summary: string,
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<AmendProposal & { proposal_id?: string }> {
  const res = await fetch("/api/v1/amend/proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      summary,
      space_id: spaceId ?? undefined,
      diff: { summary, plain: `Propose: ${summary}` },
    }),
    signal,
  });
  if (!res.ok) throw new Error(describeApiError(await res.text()));
  return (await res.json()) as AmendProposal & { proposal_id?: string };
}

export function fetchAdminOverview(signal?: AbortSignal): Promise<AdminOverview> {
  return getJson<AdminOverview>("/v1/admin/overview", signal);
}

export function fetchLibrarySources(
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<LibrarySource[]> {
  const qs = spaceId ? `?space_id=${encodeURIComponent(spaceId)}` : "";
  return getJson<LibrarySource[]>(`/v1/library/sources${qs}`, signal);
}

export type SpaceSourcesBody = {
  space_id: string;
  sources: LibrarySource[];
  count: number;
};

export function fetchSpaceSources(
  spaceId: string,
  signal?: AbortSignal,
): Promise<SpaceSourcesBody> {
  return getJson<SpaceSourcesBody>(`/v1/spaces/${encodeURIComponent(spaceId)}/sources`, signal);
}

/* ── Repository tree + previews ─────────────────────────────────────────────
 * These endpoints already existed and served real data; nothing in Studio ever
 * called them, which is why the page could only offer an Ingest button and the
 * files a user had just uploaded were nowhere to be seen.
 */

export type TreeNode = {
  id: string;
  label: string;
  kind: "folder" | "leaf";
  children?: TreeNode[] | null;
  /** "source" | "bronze" | "warehouse" — derived from the id prefix. */
  meta?: Record<string, unknown> | null;
};

export type LibraryTree = {
  space_id: string | null;
  space_name: string | null;
  nodes: TreeNode[];
};

export const PREVIEW_PAGE_SIZE = 200;

export type TablePreview = {
  table?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count?: number;
  limit?: number;
  offset?: number;
  truncated?: boolean;
  note?: string;
  kind?: string;
  source?: string | null;
  extracted_at?: string | null;
  source_kind?: string | null;
};

export function fetchLibraryTree(
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<LibraryTree> {
  const qs = spaceId ? `?space_id=${encodeURIComponent(spaceId)}` : "";
  return getJson<LibraryTree>(`/v1/library/tree${qs}`, signal);
}

export function fetchBronzePreview(
  table: string,
  limit = PREVIEW_PAGE_SIZE,
  offset = 0,
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<TablePreview> {
  const qs = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (spaceId) qs.set("space_id", spaceId);
  return getJson<TablePreview>(
    `/v1/library/bronze/${encodeURIComponent(table)}/preview?${qs}`,
    signal,
  );
}

export function fetchWarehousePreview(
  table: string,
  limit = PREVIEW_PAGE_SIZE,
  offset = 0,
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<TablePreview> {
  const qs = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (spaceId) qs.set("space_id", spaceId);
  return getJson<TablePreview>(
    `/v1/library/warehouse/${encodeURIComponent(table)}/preview?${qs}`,
    signal,
  );
}

/** Which preview endpoint a tree node id maps to, or null when it has none.
 * Silver/gold promote nodes have no row preview (LINEAGE-03). Do not map them
 * to warehouse preview — that 403s as warehouse_not_in_space.
 */
export function previewForNode(
  id: string,
): { kind: "bronze" | "warehouse"; table: string } | null {
  if (id.startsWith("silver:") || id.startsWith("gold:")) {
    return null;
  }
  if (id.startsWith("bronze:")) {
    return { kind: "bronze", table: id.slice("bronze:".length) };
  }
  if (id.startsWith("warehouse:")) {
    return { kind: "warehouse", table: id.slice("warehouse:".length) };
  }
  return null;
}

export async function fetchTablePreview(
  target: { kind: "bronze" | "warehouse"; table: string },
  limit = PREVIEW_PAGE_SIZE,
  offset = 0,
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<TablePreview> {
  return target.kind === "bronze"
    ? fetchBronzePreview(target.table, limit, offset, spaceId, signal)
    : fetchWarehousePreview(target.table, limit, offset, spaceId, signal);
}

/* ── Promote receipts (EPIC-024 LINEAGE-02/03) ────────────────────────────
 * Typed read of GET /v1/pipelines/receipts. LibraryPage renders the panel.
 * Same /api + query shape as fetchBronzePreview; errors use describeApiError
 * rather than getJson's status string, and never return null (that is
 * fetchHealth — a 403 must not look like no_receipt_yet, R-0011).
 */

export type PromoteReceipt = {
  run_id: string;
  target: string;
  sources: string[];
  source_rows: number | null;
  passed: number;
  quarantined: number;
  unmatched: number;
  reconciled: boolean;
  counts_by_reason: Record<string, number>;
  dedup_key: string[];
  lineage: string;
  table: string | null;
  quarantine_table: string | null;
};

export type PromoteReceiptState =
  | {
      target: string;
      state: "recorded";
      recorded_at: string;
      runs: number;
      receipt: PromoteReceipt;
    }
  | {
      target: string;
      state: "no_receipt_yet";
      runs: 0;
      receipt: null;
    };

export async function fetchPromoteReceipt(
  target: string,
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<PromoteReceiptState> {
  const qs = new URLSearchParams({ target });
  if (spaceId) qs.set("space_id", spaceId);
  const res = await fetch(`/api/v1/pipelines/receipts?${qs}`, { signal });
  if (!res.ok) throw new Error(describeApiError(await res.text()));
  return (await res.json()) as PromoteReceiptState;
}

/** Company default is a named scope, never "omit space_id and see every Space". */
export function verifiedQueriesPath(spaceId?: string | null): string {
  const scope = spaceId || "company-default";
  return `/v1/studio/verified-queries?space_id=${encodeURIComponent(scope)}`;
}

export type VerifiedQueryAsset = {
  asset_id: string;
  space_id: string;
  question: string;
  sql: string;
  synonyms: string[];
  created_at: string;
};

export async function fetchVerifiedQueries(
  spaceId?: string | null,
  signal?: AbortSignal,
): Promise<VerifiedQueryAsset[]> {
  const res = await fetch(`/api${verifiedQueriesPath(spaceId)}`, { signal });
  if (!res.ok) throw new Error(describeApiError(await res.text()));
  return (await res.json()) as VerifiedQueryAsset[];
}

export async function registerVerifiedQuery(
  body: { spaceId?: string | null; question: string; sql: string },
  signal?: AbortSignal,
): Promise<VerifiedQueryAsset> {
  const res = await fetch("/api/v1/studio/verified-queries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      space_id: body.spaceId || "company-default",
      question: body.question,
      sql: body.sql,
    }),
    signal,
  });
  if (!res.ok) throw new Error(describeApiError(await res.text()));
  return (await res.json()) as VerifiedQueryAsset;
}

/* ── SQL source extract (EPIC-020 SQLSRC-09 / #158) ───────────────────────
 * POST /v1/studio/sources/sql. The gate lives on the API. The password is
 * request-only: this client never writes it to storage, and errors pass it
 * through describeApiError so a leaked body cannot reach a toast.
 */

export const SQL_SOURCE_PATH = "/v1/studio/sources/sql";

export type SqlSourceKind = "sqlserver" | "mysql" | "postgresql";

export type SqlSourcePull = {
  bronze_table: string;
  source: string;
  row_count: number;
  truncated: boolean;
  extracted_at: string;
};

export type SqlSourceLink = {
  name: string;
  from: string;
  from_columns: string[];
  to: string;
  to_columns: string[];
  cardinality: "many_to_one" | "many_to_many" | "unverified" | string;
  max_fanout: number | null;
};

export type SqlSourceViolation = {
  check: string;
  subject: string;
  detail: string;
};

export type SqlSourceLinks = {
  verified: boolean;
  measured: boolean;
  reason?: string;
  links: SqlSourceLink[];
  violations: SqlSourceViolation[];
};

export type SqlGrantProposal = {
  privilege: "read";
  tables: string[];
  confirmed: boolean;
  source_id: string;
};

export type SqlSourceReceipt = {
  source: string;
  tables: SqlSourcePull[];
  skipped: string[];
  declared_primary_keys: number;
  declared_foreign_keys: number;
  links: SqlSourceLinks;
  /** Present only when DMS_READY_GRANT is on. Absent, the receipt view is unchanged. */
  grant_proposal?: SqlGrantProposal;
};

export type SqlSourceRequest = {
  kind: SqlSourceKind;
  host: string;
  database: string;
  user: string;
  password: string;
  port?: number;
  tables?: string[];
  max_rows?: number;
  space_id?: string | null;
  encrypt?: boolean;
  trust_server_certificate?: boolean;
};

/** Studio SQL form submit. R-0007: tests pin this path. */
export async function postSqlSource(
  body: SqlSourceRequest,
  signal?: AbortSignal,
): Promise<SqlSourceReceipt> {
  const res = await fetch(`/api${SQL_SOURCE_PATH}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: body.kind,
      host: body.host,
      database: body.database,
      user: body.user,
      password: body.password,
      port: body.port,
      tables: body.tables?.length ? body.tables : undefined,
      max_rows: body.max_rows,
      space_id: body.space_id || undefined,
      encrypt: body.encrypt ?? true,
      trust_server_certificate: body.trust_server_certificate ?? false,
    }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(describeApiError(text, body.password));
  }
  return (await res.json()) as SqlSourceReceipt;
}

export async function createSpace(
  name: string,
): Promise<{ space: SpaceSummary; persisted: boolean; hint?: string }> {
  const res = await fetch("/api/v1/spaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      res.status === 409 ? "A Space with that name already exists." : describeApiError(detail),
    );
  }
  return (await res.json()) as { space: SpaceSummary; persisted: boolean; hint?: string };
}

export type RevealResult = {
  ok: boolean;
  /** The file that was asked for — the same on every platform. */
  path?: string;
  /** What the OS actually surfaced: the file on Windows, its folder elsewhere. */
  opened?: string;
  action?: string;
  error?: string;
};

/** Open Explorer on an allowlisted filesystem origin_uri (REVEAL-01). */
export async function postReveal(path: string, signal?: AbortSignal): Promise<RevealResult> {
  const res = await fetch("/api/v1/library/reveal", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    signal,
  });
  if (res.status === 403) {
    return { ok: false, error: "path_not_allowlisted" };
  }
  if (!res.ok) {
    const detail = await res.text();
    return { ok: false, error: detail.slice(0, 200) || `reveal ${res.status}` };
  }
  return (await res.json()) as RevealResult;
}
