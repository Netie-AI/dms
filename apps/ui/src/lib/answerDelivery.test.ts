import { describe, expect, it } from "vitest";
import {
  MOCK_STOCK_ASK,
  MOCK_WAREHOUSE_OPS_ID,
  checkAnswerTotals,
  shareEnvelopePayload,
  shareSpacePayload,
} from "./answerDelivery";
import type { AnswerEnvelope } from "./types";

const base: AnswerEnvelope = {
  answer_id: "ans_test",
  text: "ok",
  values: [{ id: "v1", value: 100, label: "total_value_myr" }],
  badge: "L0_CERTIFIED",
  assumptions: [],
  as_of: "2026-08-28T00:00:00Z",
  contributing_sources: [],
  rows: [
    { category: "A", total_value_myr: 40 },
    { category: "B", total_value_myr: 60 },
  ],
  chart: { kind: "bar", x: "category", y: "total_value_myr" },
};

describe("answerDelivery", () => {
  it("shares a Space without inventing URLs", () => {
    const raw = shareSpacePayload({ id: MOCK_WAREHOUSE_OPS_ID, name: "Warehouse Ops" });
    const j = JSON.parse(raw) as { kind: string; space_id: string };
    expect(j.kind).toBe("dms.space");
    expect(j.space_id).toBe(MOCK_WAREHOUSE_OPS_ID);
    expect(MOCK_STOCK_ASK.toLowerCase()).toContain("stock value");
  });

  it("shares envelope badge + rows", () => {
    const j = JSON.parse(shareEnvelopePayload(base)) as {
      kind: string;
      badge: string;
      rows: unknown[];
    };
    expect(j.kind).toBe("dms.answer");
    expect(j.badge).toBe("L0_CERTIFIED");
    expect(j.rows).toHaveLength(2);
  });

  it("accuracy-check matches row sum to stated total", () => {
    const r = checkAnswerTotals(base);
    expect(r.status).toBe("ok");
    expect(r.rowSum).toBe(100);
  });

  it("accuracy-check flags mismatch", () => {
    const r = checkAnswerTotals({
      ...base,
      values: [{ id: "v1", value: 999, label: "total_value_myr" }],
    });
    expect(r.status).toBe("mismatch");
  });

  it("abstain skips without inventing a percent", () => {
    const r = checkAnswerTotals({ ...base, badge: "ABSTAIN", abstained: true, rows: [] });
    expect(r.status).toBe("skip");
  });

  it("grouped ranking does not treat the first row as a grand total", () => {
    const r = checkAnswerTotals({
      ...base,
      values: [
        { id: "v1", value: 74666465.54, label: "total_spend_myr" },
        { id: "v2", value: 59491753.67, label: "total_spend_myr" },
        { id: "v3", value: 60037691.37, label: "total_spend_myr" },
        { id: "v4", value: 448773588.67, label: "total_spend_myr" },
      ],
      rows: [
        { country: "Thailand", total_spend_myr: 74666465.54 },
        { country: "Singapore", total_spend_myr: 59491753.67 },
        { country: "China", total_spend_myr: 60037691.37 },
        { country: "Malaysia", total_spend_myr: 448773588.67 },
      ],
      chart: { kind: "bar", x: "country", y: "total_spend_myr" },
    });
    expect(r.status).toBe("ok");
    expect(r.message.toLowerCase()).toContain("grouped");
  });
});
