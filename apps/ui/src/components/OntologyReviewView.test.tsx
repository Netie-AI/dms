import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OntologyReviewView } from "./OntologyReviewView";
import type { KeyItem, LinkItem, ReviewState } from "@/lib/ontologyReview";

const k = (id: string, status = "proposed"): KeyItem => ({
  id,
  kind: "object",
  status,
  payload: { relation: "orders", key: ["order_id"] },
  provenance: { evidence: { row_count: 10, distinct_count: 10, null_count: 0 } },
});
const l = (id: string, status = "proposed"): LinkItem => ({
  id,
  kind: "link",
  status,
  payload: { from_object: "shipments", from_columns: ["order_id"], to_object: "orders", to_columns: ["order_id"] },
});

function render(patch: Partial<ReviewState>): string {
  const state: ReviewState = {
    spaceId: "s1",
    loading: false,
    measuring: false,
    items: [k("k1"), l("l1")],
    measured: null,
    persisted: true,
    refusal: null,
    pending: {},
    itemNotice: {},
    stale: [],
    ...patch,
  };
  return renderToStaticMarkup(
    <OntologyReviewView state={state} spaceName="Retail" onMeasure={() => {}} onPress={() => {}} />,
  );
}
const count = (html: string, testid: string) => html.split(`data-testid="${testid}"`).length - 1;

describe("OntologyReviewView", () => {
  it("offers one Approve and one Reject per item, each naming its item, and no bulk control", () => {
    const html = render({});
    expect(count(html, "onto-review-approve")).toBe(2);
    expect(count(html, "onto-review-reject")).toBe(2);
    expect(html).toContain('aria-label="Approve key order_id of orders"');
    expect(html).not.toMatch(/Approve all|Reject all|every|select all/i);
  });

  it("offers no Approve, Reject or Measure without a steward grant, and says why", () => {
    const html = render({ refusal: { kind: "steward_grant_required" } });
    expect(count(html, "onto-review-approve")).toBe(0);
    expect(count(html, "onto-review-reject")).toBe(0);
    expect(count(html, "onto-review-measure")).toBe(0);
    expect(count(html, "onto-review-steward-required")).toBe(1);
    expect(count(html, "onto-review-item")).toBe(0);
  });

  it("shows the in-memory notice only when persisted is false", () => {
    expect(count(render({ persisted: false }), "onto-review-memory-notice")).toBe(1);
    expect(count(render({ persisted: true }), "onto-review-memory-notice")).toBe(0);
    expect(count(render({ persisted: null }), "onto-review-memory-notice")).toBe(0);
  });

  it("shows the status the state holds, never one it is waiting for", () => {
    const html = render({ pending: { k1: "approve" } });
    expect(html).toContain("Approving…");
    expect(html).toMatch(/data-item-id="k1"[^>]*data-status="proposed"/);
    expect(html).not.toContain(">approved<");
  });

  it("takes Approve off a stale item but keeps Reject, and keeps the 409 visible", () => {
    const html = render({
      stale: "all",
      refusal: { kind: "no_file_tables", skipped: [] },
    });
    expect(count(html, "onto-review-approve")).toBe(0);
    expect(count(html, "onto-review-reject")).toBe(2);
    expect(count(html, "onto-review-no-file-tables")).toBe(1);
    expect(count(html, "onto-review-stale")).toBe(2);
  });

  it("disables every press and Measure while a measurement runs", () => {
    const html = render({ measuring: true });
    expect(html).toContain("Measuring…");
    for (const m of html.match(/<button[^>]*data-testid="onto-review-(approve|reject|measure)"[^>]*>/g) ?? []) {
      expect(m).toContain("disabled");
    }
  });

  it("a rejected link says it is rejected, not that it waits for keys", () => {
    const html = render({ items: [k("k1"), l("l1", "revoked")] });
    expect(html).toContain("Rejected: this link is not used");
    expect(html).not.toContain("not usable until both keys are approved");
  });

  it("never says verified or certified for a profile item", () => {
    const html = render({ items: [k("k1", "approved"), l("l1", "approved")] });
    expect(html).not.toMatch(/verif|certif/i);
  });
});
