/** DMS API client — browser talks only to DMS (proxied /api). Never Cortex. */

import type {
  AdminOverview,
  AnswerEnvelope,
  LibrarySource,
  OntologyAction,
  OntologyBundle,
  OntologyFunction,
  OntologyGraphEdge,
  OntologyGraphNode,
  OntologyLink,
  OntologyMetric,
  OntologyObject,
  OntologySummary,
  RunsBody,
  SpaceSummary,
  TrustRunDetailItem,
  TrustSummary,
} from "./types";

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

/** Turn a raw API error body into a sentence a steward can act on. */
export function describeApiError(body: string): string {
  let reason = body.trim();
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") reason = parsed.detail;
  } catch {
    /* keep raw body */
  }
  if (reason === "gate_unavailable") {
    return "Cortex gate is unreachable. Start Cortex before writing -- an ungated change is refused.";
  }
  if (reason === "gate_task_unknown") {
    return "Cortex does not know this task yet. The write is refused rather than applied ungated.";
  }
  return reason;
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
};

export async function postAsk(
  payload: AskPayload,
  signal?: AbortSignal,
): Promise<AnswerEnvelope> {
  const res = await fetch("/api/v1/chat/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: payload.question,
      space_id: payload.space_id ?? undefined,
      session_id: payload.session_id ?? undefined,
      grounded_tables: payload.grounded_tables?.length
        ? payload.grounded_tables
        : undefined,
    }),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`ask ${res.status}: ${detail}`);
  }
  return (await res.json()) as AnswerEnvelope;
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

type SectionBody<K extends string, T> = { ok: boolean; hint?: string; error?: string } & {
  [P in K]?: T[];
};

function section<K extends string, T>(body: SectionBody<K, T>, key: K): T[] {
  return (body[key] as T[] | undefined) ?? [];
}

/** One page, one round of fetches — the ontology is small and always shown whole. */
export async function fetchOntology(signal?: AbortSignal): Promise<OntologyBundle> {
  const [summary, objects, links, actions, functions, metrics, graph] = await Promise.all([
    getJson<OntologySummary>("/v1/ontology", signal),
    getJson<SectionBody<"object_types", OntologyObject>>("/v1/ontology/objects", signal),
    getJson<SectionBody<"link_types", OntologyLink>>("/v1/ontology/links", signal),
    getJson<SectionBody<"action_types", OntologyAction>>("/v1/ontology/actions", signal),
    getJson<SectionBody<"functions", OntologyFunction>>("/v1/ontology/functions", signal),
    getJson<SectionBody<"metrics", OntologyMetric>>("/v1/ontology/metrics", signal),
    getJson<{
      ok: boolean;
      nodes?: OntologyGraphNode[];
      edges?: OntologyGraphEdge[];
    }>("/v1/ontology/graph", signal),
  ]);
  return {
    summary,
    objects: section(objects, "object_types"),
    links: section(links, "link_types"),
    actions: section(actions, "action_types"),
    functions: section(functions, "functions"),
    metrics: section(metrics, "metrics"),
    graph: { nodes: graph.nodes ?? [], edges: graph.edges ?? [] },
  };
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

/** Which preview endpoint a tree node id maps to, or null when it has none. */
export function previewForNode(
  id: string,
): { kind: "bronze" | "warehouse"; table: string } | null {
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

/* ── Promote receipts (EPIC-024 LINEAGE-02) ────────────────────────────────
 * Typed read of GET /v1/pipelines/receipts. Nothing renders yet (tickets 3-4).
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
