import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  ontologyBundle,
  ontologyCountsMeasured,
  ontologyHeldView,
  ontologyHonestCard,
  ontologyReadPath,
  ontologyWindow,
} from "./ontologyRead";

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
    expect(missing?.title).toBe("Pick a Space");
    expect(ontologyHonestCard({ ok: true, reason: "" })).toBeNull();
  });

  it("the page passes activeSpaceId and renders the honest card", () => {
    const page = readFileSync(join(here, "../pages/OntologyPage.tsx"), "utf8");
    const api = readFileSync(join(here, "api.ts"), "utf8");
    expect(page).toContain("fetchOntology(activeSpaceId, signal)");
    expect(page).toContain('data-testid="ontology-honest-card"');
    expect(page).toContain('data-testid="ontology-counts"');
    expect(page).toContain('data-testid="ontology-loading"');
    expect(page).toContain('data-testid="ontology-retry"');
    expect(page).toContain("ontologyHeldView(");
    expect(page).toContain("ontologyCountsMeasured(");
    expect(page).toContain("ontologyWindow(");
    expect(page).toContain("Load more");
    expect(page).toContain('data-testid="ontology-window"');
    expect(api).toContain("ontologyReadPath(");
    expect(api).toContain("ontologyBundle(");
    expect(api).not.toContain('getJson<OntologySummary>("/v1/ontology", signal)');
    expect(api).not.toContain('ontologyReadPath("objects"');
  });

  it("reads counts from the one summary body", () => {
    const bundle = ontologyBundle({
      ok: true,
      counts: {
        object_types: 3,
        properties: 1,
        sensitive_properties: 0,
        link_types: 2,
        action_types: 0,
        action_tools: 0,
        action_events: 0,
        functions: 4,
        metrics: 1,
      },
      object_types: [],
      link_types: [],
    });
    expect(bundle.summary.counts?.object_types).toBe(3);
    expect(bundle.summary.counts?.link_types).toBe(2);
    expect(bundle.summary.counts?.functions).toBe(4);
    expect(bundle.objects).toEqual([]);
    expect(bundle.graph.nodes).toEqual([]);
  });

  it("a connect failure keeps the last lists and does not count them as zero", () => {
    const good = {
      summary: { ok: true, reason: "", counts: { object_types: 3 } },
      objects: [{ id: "sale" }],
    };
    const down = {
      summary: { ok: false, reason: "store_unreadable", counts: { object_types: 0 } },
      objects: [] as { id: string }[],
    };
    expect(ontologyCountsMeasured(down.summary)).toBe(false);
    expect(ontologyCountsMeasured({ ok: false, reason: "empty_store" })).toBe(false);
    expect(ontologyCountsMeasured({ ok: false, reason: "space_required" })).toBe(false);
    expect(ontologyCountsMeasured(good.summary)).toBe(true);
    const kept = ontologyHeldView(down, good, false);
    expect(kept?.objects).toEqual([{ id: "sale" }]);
    expect(kept?.summary.counts?.object_types).toBe(3);
    const loading = ontologyHeldView(null, good, true);
    expect(loading?.objects).toEqual([{ id: "sale" }]);
    const firstFail = ontologyHeldView(down, null, false);
    expect(firstFail).toBe(down);
    expect(ontologyCountsMeasured(firstFail?.summary)).toBe(false);
  });

  it("a long list says how many are on screen and keeps the rest", () => {
    const few = ontologyWindow(["a", "b", "c"], 100);
    expect(few.rows).toEqual(["a", "b", "c"]);
    expect(few.label).toBeNull();
    const many = Array.from({ length: 150 }, (_, i) => i);
    const page = ontologyWindow(many, 100);
    expect(page.rows).toHaveLength(100);
    expect(page.label).toBe("showing 100 of 150");
    expect(ontologyWindow(many, 150).label).toBeNull();
    expect(ontologyWindow(many, 150).rows).toHaveLength(150);
  });
});
