import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ConstraintTracePanel } from "../components/ConstraintTracePanel";
import {
  countMalformedStages,
  parseConstraintTrace,
  summariseTrace,
  traceHeadline,
  type ConstraintStage,
} from "./constraintTrace";

function stage(over: Partial<ConstraintStage> & { type: ConstraintStage["type"] }): ConstraintStage {
  return {
    constraint_id: `c_${over.type}`,
    candidate: "revenue",
    binding: "gold.sales.revenue_myr",
    evidence: ["semantic_layer: metric revenue_myr"],
    status: "CERTIFIED",
    reasons: [],
    ...over,
  };
}

describe("parseConstraintTrace", () => {
  it("returns the fixed cascade order, not arrival order", () => {
    const stages = parseConstraintTrace([
      stage({ type: "sql" }),
      stage({ type: "sense" }),
      stage({ type: "envelope" }),
      stage({ type: "geo" }),
    ]);
    expect(stages.map((s) => s.type)).toEqual(["sense", "geo", "sql", "envelope"]);
  });

  it("drops an unknown status rather than treating it as certified", () => {
    const raw = [
      stage({ type: "sense" }),
      { ...stage({ type: "geo" }), status: "PASS" },
      { ...stage({ type: "sql" }), type: "vibes" },
    ];
    const stages = parseConstraintTrace(raw);
    expect(stages.map((s) => s.type)).toEqual(["sense"]);
    expect(stages.every((s) => s.status === "CERTIFIED")).toBe(true);
    expect(countMalformedStages(raw)).toBe(2);
  });

  it("drops an item missing a required field", () => {
    const raw = [
      stage({ type: "sense" }),
      { constraint_id: "c_geo", type: "geo", candidate: "KL", status: "CERTIFIED" },
    ];
    expect(parseConstraintTrace(raw).map((s) => s.type)).toEqual(["sense"]);
    expect(countMalformedStages(raw)).toBe(1);
  });

  it("keeps the first verdict when a stage is reported twice", () => {
    const raw = [
      stage({ type: "geo", status: "ABSTAIN", reasons: ["no geo column"] }),
      stage({ type: "geo", status: "CERTIFIED" }),
    ];
    const stages = parseConstraintTrace(raw);
    expect(stages).toHaveLength(1);
    expect(stages[0].status).toBe("ABSTAIN");
    expect(countMalformedStages(raw)).toBe(1);
  });

  it("treats an absent or non-list trace as no stages", () => {
    expect(parseConstraintTrace(undefined)).toEqual([]);
    expect(parseConstraintTrace(null)).toEqual([]);
    expect(parseConstraintTrace({ stages: [] })).toEqual([]);
  });
});

describe("summariseTrace", () => {
  it("an ABSTAIN sets blockedAt and carries its reason", () => {
    const summary = summariseTrace(
      parseConstraintTrace([
        stage({ type: "sense" }),
        stage({ type: "asset_class" }),
        stage({
          type: "geo",
          status: "ABSTAIN",
          binding: null,
          reasons: ["no geo column bound to 'KL'"],
        }),
      ]),
    );
    expect(summary).toEqual({
      ran: 3,
      certified: 2,
      blockedAt: "geo",
      blockedReason: "no geo column bound to 'KL'",
    });
    expect(traceHeadline(summary)).toBe("Blocked at Geo: no geo column bound to 'KL'");
  });

  it("blockedAt is the first non-CERTIFIED stage in cascade order", () => {
    const summary = summariseTrace(
      parseConstraintTrace([
        stage({ type: "sql", status: "REFUSE", reasons: ["hostile sql"] }),
        stage({ type: "sense", status: "ABSTAIN", reasons: ["term not in ontology"] }),
      ]),
    );
    expect(summary.blockedAt).toBe("sense");
    expect(summary.blockedReason).toBe("term not in ontology");
  });

  it("an empty trace is 'no cascade ran', never certified", () => {
    const summary = summariseTrace(parseConstraintTrace(undefined));
    expect(summary).toEqual({ ran: 0, certified: 0, blockedAt: null, blockedReason: "" });
    expect(traceHeadline(summary)).toBe("No cascade ran. Nothing here is certified.");
    expect(traceHeadline(summary)).not.toMatch(/^\d+ of/);
  });

  it("a blocking stage with no reasons still says so rather than nothing", () => {
    const summary = summariseTrace(
      parseConstraintTrace([stage({ type: "sense", status: "REFUSE", reasons: [] })]),
    );
    expect(summary.blockedReason).toBe("No reason given by the cascade.");
  });
});

describe("ConstraintTracePanel", () => {
  function render(trace: unknown): string {
    return renderToStaticMarkup(createElement(ConstraintTracePanel, { trace }));
  }

  it("empty trace renders 'no cascade ran' and no stage rows", () => {
    const html = render(undefined);
    expect(html).toContain('data-testid="cca-none"');
    expect(html).toContain("No cascade ran");
    expect(html).not.toContain('data-status="CERTIFIED"');
    expect(html).not.toContain("--color-badge-ok");
  });

  it("paints the abstain reason and does not paint the stage green", () => {
    const html = render([
      stage({ type: "sense" }),
      stage({
        type: "geo",
        candidate: "KL",
        status: "ABSTAIN",
        binding: null,
        evidence: ["column geo_code values: MY-10, MY-14"],
        reasons: ["'KL' does not match the geo_code encoding"],
      }),
    ]);
    expect(html).toContain('data-testid="cca-blocked-reason-geo"');
    expect(html).toContain("&#x27;KL&#x27; does not match the geo_code encoding");
    expect(html).toContain("not bound");
    expect(html).toContain("column geo_code values: MY-10, MY-14");
    const geoRow = html.split('data-testid="cca-stage-geo"')[1]?.split("</li>")[0] ?? "";
    expect(geoRow).toContain("--color-warn");
    expect(geoRow).not.toContain("--color-badge-ok");
  });

  it("a REFUSE reads differently from an ABSTAIN", () => {
    const refuse = render([stage({ type: "sense", status: "REFUSE", reasons: ["policy"] })]);
    const abstain = render([stage({ type: "sense", status: "ABSTAIN", reasons: ["policy"] })]);
    expect(refuse).toContain('data-status="REFUSE"');
    expect(refuse).toContain("--color-danger");
    expect(abstain).toContain('data-status="ABSTAIN"');
    expect(abstain).not.toContain("--color-danger");
    expect(refuse).not.toEqual(abstain);
  });

  it("shows candidate, binding and status per certified stage", () => {
    const html = render([stage({ type: "sense", candidate: "revenue" })]);
    expect(html).toContain('data-testid="cca-candidate-sense">revenue');
    expect(html).toContain('data-testid="cca-binding-sense">gold.sales.revenue_myr');
    expect(html).toContain('data-testid="cca-status-sense"');
    expect(html).toContain("1 of 7 stages certified.");
  });

  it("counts unreadable items instead of hiding them", () => {
    const html = render([stage({ type: "sense" }), { type: "geo", status: "MAYBE" }]);
    expect(html).toContain('data-testid="cca-dropped"');
    expect(html).toContain("1 trace item was unreadable and dropped.");
  });
});
