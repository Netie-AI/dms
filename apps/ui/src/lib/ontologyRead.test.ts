import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ontologyHonestCard, ontologyReadPath } from "./ontologyRead";

const here = dirname(fileURLToPath(import.meta.url));
const FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd";

describe("ontology reads name the active Space", () => {
  it("puts space_id on every section and never invents Finance", () => {
    expect(ontologyReadPath("", OPS)).toBe(`/v1/ontology?space_id=${OPS}`);
    expect(ontologyReadPath("graph", OPS)).toBe(`/v1/ontology/graph?space_id=${OPS}`);
    expect(ontologyReadPath("objects", null)).toBe("/v1/ontology/objects");
    expect(ontologyReadPath("metrics", "  ")).not.toContain(FINANCE);
    expect(ontologyReadPath("graph", null)).not.toContain("space_id=");
  });

  it("names an empty or unreadable store and stays quiet on a real payload", () => {
    const empty = ontologyHonestCard({
      ok: false,
      reason: "empty_store",
      hint: "This Space has no approved ontology in the store.",
    });
    expect(empty?.reason).toBe("empty_store");
    expect(empty?.title).toBe("No ontology for this Space");
    const down = ontologyHonestCard({ ok: false, reason: "store_unreadable" });
    expect(down?.title).toBe("Ontology store could not be read");
    const missing = ontologyHonestCard({ ok: false, reason: "space_required" });
    expect(missing?.title).toBe("No Space selected");
    expect(ontologyHonestCard({ ok: true, reason: "" })).toBeNull();
  });

  it("the page passes activeSpaceId and renders the honest card", () => {
    const page = readFileSync(join(here, "../pages/OntologyPage.tsx"), "utf8");
    const api = readFileSync(join(here, "api.ts"), "utf8");
    expect(page).toContain("fetchOntology(activeSpaceId, signal)");
    expect(page).toContain('data-testid="ontology-honest-card"');
    expect(api).toContain("ontologyReadPath(");
    expect(api).not.toContain('getJson<OntologySummary>("/v1/ontology", signal)');
  });
});
