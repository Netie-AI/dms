import { describe, expect, it } from "vitest";
import { CEO_NAV_IDS, ceoSafeHref, navIdsForMode } from "./productMode";

describe("product modes", () => {
  it("cream (CEO) only exposes ask + database + trust", () => {
    const ids = navIdsForMode("cream");
    expect(ids).not.toBeNull();
    expect([...ids!].sort()).toEqual([...CEO_NAV_IDS].sort());
    expect(ids!.has("studio")).toBe(false);
    expect(ids!.has("admin")).toBe(false);
    expect(ids!.has("runs")).toBe(false);
  });

  it("graphite (operator) does not filter the nav", () => {
    expect(navIdsForMode("graphite")).toBeNull();
  });

  it("CEO empty-state pack has a live-curated spend ask and a typo trap", async () => {
    const { SUGGESTED_QUESTIONS } = await import("./fixtures");
    expect(SUGGESTED_QUESTIONS.some((q) => q.includes("spend by supplier country"))).toBe(true);
    expect(SUGGESTED_QUESTIONS.some((q) => q.includes("Top 5 selling SKUs"))).toBe(true);
    expect(SUGGESTED_QUESTIONS.some((q) => q.includes("categoty"))).toBe(true);
  });

  it("Ask mode rewrites operator routes to Library or Trust", () => {
    expect(ceoSafeHref("cream", "/studio")).toBe("/library");
    expect(ceoSafeHref("cream", "/ontology")).toBe("/library");
    expect(ceoSafeHref("cream", "/audit")).toBe("/trust");
    expect(ceoSafeHref("graphite", "/studio")).toBe("/studio");
  });
});
