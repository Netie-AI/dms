import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SpaceBenchView } from "@/pages/SpaceBenchPanel";
import type { SpaceBenchBody } from "@/lib/api";

const body: SpaceBenchBody = {
  enabled: true,
  space_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  items: [
    {
      id: "b1",
      question: "bench synth count",
      gold_sql: "SELECT qty FROM bench_sku",
      status: "valid",
      invalid_reason: "",
      source: "steward",
    },
  ],
  latest_run: {
    id: "run1",
    status: "done",
    progress_done: 1,
    progress_total: 1,
    tally: {
      n: 1,
      graded: 1,
      coverage: 1,
      correct: 1,
      WRONG: 0,
      clarify: 0,
      reconfirm: 0,
      abstain: 0,
      refusal_wrong: 0,
      invalid_gold: 0,
    },
    results: [
      {
        id: "r1",
        question: "bench synth count",
        served_sql: "SELECT qty FROM bench_sku",
        outcome: "correct",
        reason: "match",
        gold_n: 1,
        served_n: 1,
        review: null,
        review_note: "",
      },
    ],
  },
};

describe("FEAT-BENCH-SPACE-01", () => {
  it("shows WRONG before coverage", () => {
    const html = renderToStaticMarkup(createElement(SpaceBenchView, { body }));
    const wrongAt = html.indexOf("WRONG 0");
    const coverageAt = html.indexOf("coverage");
    expect(wrongAt).toBeGreaterThan(-1);
    expect(coverageAt).toBeGreaterThan(wrongAt);
    expect(html).toContain("bench synth count");
    expect(html).toContain("rows 1 compared with 1");
    expect(html).not.toContain("trust-bench-cancel");
  });
});
