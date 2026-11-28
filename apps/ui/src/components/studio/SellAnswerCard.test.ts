import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SellAnswerCard } from "./SellAnswerCard";
import type { AnswerEnvelope } from "@/lib/types";

function envelope(): AnswerEnvelope {
  return {
    answer_id: "ans_sell",
    text: "Here is stock by warehouse.",
    values: [],
    badge: "L2_VALIDATED",
    abstained: false,
    assumptions: [],
    as_of: "2026-11-28T00:00:00Z",
    contributing_sources: [],
    sql_used: "SELECT location_id, quantity_kg FROM inventory",
    rows: [
      { location_id: "WH-A", quantity_kg: 1200 },
      { location_id: "WH-B", quantity_kg: 3400 },
    ],
    sell_profile: true,
    sell_checks: ["grant", "read_only", "explain", "shape"],
    sell_row_cap: 20,
    chart_spec: { kind: "bar", x: "location_id", y: ["quantity_kg"], series: null },
  };
}

describe("SellAnswerCard", () => {
  it("puts the answer first and keeps SQL and rows behind buttons", () => {
    const markup = renderToStaticMarkup(createElement(SellAnswerCard, { envelope: envelope() }));
    expect(markup.indexOf("Here is stock by warehouse.")).toBeGreaterThan(-1);
    expect(markup.indexOf("Here is stock by warehouse.")).toBeLessThan(markup.indexOf("See SQL"));
    expect(markup).toContain("See SQL");
    expect(markup).toContain("See rows");
    expect(markup).toContain("How I checked");
    expect(markup).toContain('data-testid="sell-chart"');
    expect(markup).toContain("answer-landed");
    expect(markup).not.toContain("SELECT location_id");
    expect(markup).not.toContain('data-testid="sell-rows"');
    expect(markup).not.toContain('data-testid="sell-sql"');
  });

  it("shows a clarifying question and its options", () => {
    const env = envelope();
    env.abstained = true;
    env.badge = "ABSTAIN";
    env.text = "Which warehouse should I use?";
    env.sql_used = undefined;
    env.rows = [];
    env.chart_spec = undefined;
    env.clarify = {
      clarify_id: "sell",
      question: "Which warehouse should I use?",
      readings: [
        { reading_id: "r1", label: "Warehouse A" },
        { reading_id: "r2", label: "Warehouse B" },
      ],
    };
    const markup = renderToStaticMarkup(createElement(SellAnswerCard, { envelope: env }));
    expect(markup).toContain("Which warehouse should I use?");
    expect(markup).toContain("Warehouse A");
    expect(markup).toContain("Warehouse B");
    expect(markup).toContain('data-testid="clarify-card"');
  });
});
