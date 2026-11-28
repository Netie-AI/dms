/** Ontology page reads. A missing Space id is left missing. Never a default Space. */

import type {
  OntologyAction,
  OntologyBundle,
  OntologyFunction,
  OntologyGraphEdge,
  OntologyGraphNode,
  OntologyLink,
  OntologyMetric,
  OntologyObject,
  OntologySummary,
} from "./types";

/** The one GET /v1/ontology body. Counts and the lists travel together. */
export type OntologyReadBody = OntologySummary & {
  object_types?: OntologyObject[];
  link_types?: OntologyLink[];
  action_types?: OntologyAction[];
  functions?: OntologyFunction[];
  metrics?: OntologyMetric[];
  nodes?: OntologyGraphNode[];
  edges?: OntologyGraphEdge[];
};

export function ontologyBundle(body: OntologyReadBody): OntologyBundle {
  return {
    summary: body,
    objects: body.object_types ?? [],
    links: body.link_types ?? [],
    actions: body.action_types ?? [],
    functions: body.functions ?? [],
    metrics: body.metrics ?? [],
    graph: { nodes: body.nodes ?? [], edges: body.edges ?? [] },
  };
}

const TITLES: Record<string, string> = {
  space_required: "Pick a Space",
  empty_store: "No ontology for this Space",
  store_unreadable: "Ontology store could not be read",
};

export function ontologyReadPath(section: string, spaceId?: string | null): string {
  const base = section ? `/v1/ontology/${section}` : "/v1/ontology";
  const id = (spaceId ?? "").trim();
  if (!id) return base;
  return `${base}?space_id=${encodeURIComponent(id)}`;
}

/** Tiles only after a successful read. space_required and a failed read are not zeros. */
export function ontologyCountsMeasured(
  summary: { ok?: boolean; reason?: string } | null | undefined,
): boolean {
  return summary?.ok === true;
}

/** How many rows a list shows before Load more. The rest stay in the payload. */
export const ONTOLOGY_PAGE = 100;

/** A window, not a silent cut. `label` is null when every row is on screen. */
export function ontologyWindow<T>(
  items: readonly T[],
  shown: number,
): { rows: T[]; label: string | null } {
  const total = items.length;
  const n = Math.min(total, Math.max(0, shown));
  const rows = items.slice(0, n);
  if (n >= total) return { rows, label: null };
  return { rows, label: `showing ${n} of ${total}` };
}

/**
 * Keep the last good lists while a read is in flight or the store timed out.
 * A 3s connect failure must not replace objects that were already on screen.
 */
export function ontologyHeldView<T extends { summary: { ok?: boolean; reason?: string } }>(
  incoming: T | null,
  held: T | null,
  loading: boolean,
): T | null {
  const failed = incoming?.summary.reason === "store_unreadable";
  if ((failed || loading || incoming == null) && held?.summary.ok) return held;
  return incoming;
}

/** Card for ok:false. A store payload (ok true) is not a card. */
export function ontologyHonestCard(
  summary: { ok?: boolean; reason?: string; hint?: string } | null | undefined,
): { reason: string; title: string; hint: string } | null {
  if (!summary || summary.ok !== false) return null;
  const reason = summary.reason || "store_unreadable";
  return {
    reason,
    title: TITLES[reason] ?? "Ontology unavailable",
    hint:
      summary.hint ||
      "The ontology store did not answer. No pack is shown in its place.",
  };
}
