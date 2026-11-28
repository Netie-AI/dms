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

function htmlFor(next: SpaceBenchBody): string {
  return renderToStaticMarkup(createElement(SpaceBenchView, { body: next }));
}

describe("FEAT-BENCH-SPACE-01", () => {
  it("says not measured when there is no run or no tally", () => {
    const run = body.latest_run;
    if (!run) throw new Error("fixture run missing");
    const cases: SpaceBenchBody[] = [
      { ...body, latest_run: null },
      { ...body, latest_run: { ...run, tally: undefined as unknown as typeof run.tally } },
    ];
    for (const next of cases) {
      const html = htmlFor(next);
      expect(html).toContain("Not measured yet");
      expect(html).toContain("Run benchmarks");
      expect(html).not.toContain("WRONG");
      expect(html).not.toContain("(0/0)");
      expect(html).not.toContain("0/0");
    }
  });

  it("a missing WRONG field never renders as 0", () => {
    const run = body.latest_run;
    if (!run) throw new Error("fixture run missing");
    const tally = { ...run.tally };
    delete (tally as { WRONG?: number }).WRONG;
    const html = htmlFor({ ...body, latest_run: { ...run, tally } });
    expect(html).toContain("Not measured yet");
    expect(html).toContain("Run benchmarks");
    expect(html).not.toMatch(/WRONG\s+0/);
    expect(html).not.toContain("WRONG");
    expect(html).not.toContain("(0/0)");
  });

  it("does not fill a missing fraction with zeros", () => {
    const run = body.latest_run;
    if (!run) throw new Error("fixture run missing");
    const tally = { ...run.tally };
    delete (tally as { correct?: number; graded?: number }).correct;
    delete (tally as { correct?: number; graded?: number }).graded;
    const html = htmlFor({ ...body, latest_run: { ...run, tally } });
    expect(html).toContain("WRONG 0");
    expect(html).not.toContain("(0/0)");
    expect(html).not.toContain("/0");
  });

  it("shows a recorded zero before coverage", () => {
    const html = htmlFor(body);
    const wrongAt = html.indexOf("WRONG 0");
    const coverageAt = html.indexOf("coverage");
    expect(wrongAt).toBeGreaterThan(-1);
    expect(coverageAt).toBeGreaterThan(wrongAt);
    expect(html).toContain("bench synth count");
    expect(html).toContain("rows 1 compared with 1");
    expect(html).not.toContain("trust-bench-cancel");
    expect(html).not.toContain("Not measured yet");
  });
});
