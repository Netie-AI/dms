/** Pure helpers for the ontology screens, kept out of the components so they can be unit tested. */

import type { SpaceOntologyLink, SpaceOntologyViolation } from "./types";

type Edge = {
  id: string;
  source: string;
  target: string;
  source_property: string;
  target_property: string;
  cardinality: string;
};

export type Neighbour = {
  edgeId: string;
  /** The object on the other end of the link. */
  other: string;
  /** Both ends spelled out, so direction is never implied. */
  via: string;
  cardinality: string;
};

/** Every link that touches `id`, as a row that re-centres on the other end. */
export function neighbourhood(id: string, edges: Edge[]): Neighbour[] {
  return edges
    .filter((e) => e.source === id || e.target === id)
    .map((e) => ({
      edgeId: e.id,
      other: e.source === id ? e.target : e.source,
      via: `${e.source}.${e.source_property} → ${e.target}.${e.target_property}`,
      cardinality: e.cardinality,
    }));
}

/** "order -> customer on customer_id = id"; an unlanded link has no columns to name. */
export function linkSentence(l: SpaceOntologyLink): string {
  const cols = l.from_columns.length
    ? l.from_columns.map((c, i) => `${c} = ${l.to_columns[i] ?? "?"}`).join(", ")
    : "unknown columns";
  return `${l.from} → ${l.to} on ${cols}`;
}

/** "no_primary_key" + detail -> "No primary key: the source declares ..." */
export function violationText(v: SpaceOntologyViolation): string {
  const label = v.check.replace(/_/g, " ");
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}: ${v.detail}`;
}
