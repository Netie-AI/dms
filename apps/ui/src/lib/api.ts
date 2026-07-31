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

export async function fetchHealth(signal?: AbortSignal): Promise<HealthBody | null> {
  try {
    const res = await fetch("/api/health", { signal });
    if (!res.ok) return null;
    return (await res.json()) as HealthBody;
  } catch {
    return null;
  }
}

export async function fetchSpaces(signal?: AbortSignal): Promise<SpaceSummary[]> {
  const res = await fetch("/api/v1/spaces", { signal });
  if (!res.ok) throw new Error(`spaces ${res.status}`);
  return (await res.json()) as SpaceSummary[];
}

export type AskPayload = {
  question: string;
  space_id?: string | null;
  session_id?: string | null;
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

export function fetchRuns(kind?: string, signal?: AbortSignal): Promise<RunsBody> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return getJson<RunsBody>(`/v1/runs${qs}`, signal);
}

export function fetchAdminOverview(signal?: AbortSignal): Promise<AdminOverview> {
  return getJson<AdminOverview>("/v1/admin/overview", signal);
}

export function fetchLibrarySources(signal?: AbortSignal): Promise<LibrarySource[]> {
  return getJson<LibrarySource[]>("/v1/library/sources", signal);
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
    throw new Error(res.status === 409 ? "A Space with that name already exists." : detail);
  }
  return (await res.json()) as { space: SpaceSummary; persisted: boolean; hint?: string };
}
