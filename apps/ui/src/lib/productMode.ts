/** Product modes: cream = CEO / Claude-white ask; graphite = operator appliance. */

export type ProductMode = "cream" | "graphite";

export const CEO_NAV_IDS = ["chat", "spaces", "library", "trust"] as const;

export function storedProductMode(): ProductMode {
  try {
    return window.localStorage.getItem("dms-theme") === "graphite" ? "graphite" : "cream";
  } catch {
    return "cream";
  }
}

export function navIdsForMode(mode: ProductMode): ReadonlySet<string> | null {
  if (mode === "cream") return new Set(CEO_NAV_IDS);
  return null;
}

/** Ask mode has no Studio/Ontology/Audit. Send the CEO to Library or Trust. */
export function ceoSafeHref(
  mode: ProductMode,
  path: "/studio" | "/ontology" | "/audit",
): string {
  if (mode !== "cream") return path;
  if (path === "/audit") return "/trust";
  return "/library";
}
