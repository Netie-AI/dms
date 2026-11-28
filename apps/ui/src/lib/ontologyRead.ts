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
  space_required: "No Space selected",
  empty_store: "No ontology for this Space",
  store_unreadable: "Ontology store could not be read",
};

export function ontologyReadPath(section: string, spaceId?: string | null): string {
  const base = section ? `/v1/ontology/${section}` : "/v1/ontology";
  const id = (spaceId ?? "").trim();
  if (!id) return base;
  return `${base}?space_id=${encodeURIComponent(id)}`;
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
