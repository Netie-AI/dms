import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import {
  ClarifyPanel,
  InsightTiersView,
  TrustBadge,
  clarifyRunBody,
  type ClarifyOption,
} from "./ClarifyPanel";
import { ResultView, type StudioAskEnvelope } from "./ResultView";

const here = dirname(fileURLToPath(import.meta.url));

const option: ClarifyOption = {
  id: "opt_abc",
  text: "Compute total quantity moved by supplier from transaction.",
  sql_preview: "SELECT SUM(f.quantity_kg) AS spend_kg FROM transactions f",
  plan: {
    measure: "spend_kg",
    entity: { object: "supplier", column: "supplier_id" },
    filter: null,
    time_grain: null,
  },
};

function panel(pickedId: string | null, onRun = vi.fn()) {
  return renderToStaticMarkup(
    createElement(ClarifyPanel, {
      options: [option],
      pickedId,
      onPick: () => undefined,
      onRephrase: () => undefined,
      onRun,
    }),
  );
}

describe("ASK-GUIDE-01 clarify buttons", () => {
  it("renders readings and rephrase, and does not show Run until a pick", () => {
    const markup = panel(null);
    expect(markup).toContain('data-testid="clarify-option"');
    expect(markup).toContain("Compute total quantity moved by supplier from transaction.");
    expect(markup).toContain("SELECT SUM");
    expect(markup).toContain("None of these, let me rephrase");
    expect(markup).not.toContain('data-testid="clarify-run"');
    expect(markup).not.toContain("Run?");
  });

  it("a pick shows the confirm card and Run sends the id and plan, not SQL", () => {
    const markup = panel(option.id);
    expect(markup).toContain('data-testid="clarify-confirm"');
    expect(markup).toContain(
      "Compute total quantity moved by supplier from transaction. Run?",
    );
    expect(markup).toContain('data-testid="clarify-run"');
    const body = clarifyRunBody(option);
    expect(body).toEqual({ option_id: "opt_abc", plan: option.plan });
    expect(JSON.stringify(body)).not.toContain("SELECT");
    expect(body).not.toHaveProperty("sql_preview");
  });

  it("ResultView does not auto-run and starts with no confirm card", () => {
    const src = readFileSync(join(here, "ResultView.tsx"), "utf8");
    expect(src).not.toMatch(/useEffect\([\s\S]*onClarifyRun/);
    const envelope = {
      badge: "ABSTAIN",
      abstained: true,
      text: "I cannot certify that.",
      assumptions: ["GEN-01: question is too vague or time-unbounded to ground"],
      clarify: { options: [option] },
    } as StudioAskEnvelope;
    const markup = renderToStaticMarkup(createElement(ResultView, { envelope }));
    expect(markup).toContain('data-testid="clarify-option"');
    expect(markup).toContain("None of these, let me rephrase");
    expect(markup).not.toContain('data-testid="clarify-run"');
  });

  it("shows the trust badge and omits tier 2 when SQL did not produce it", () => {
    const badge = renderToStaticMarkup(createElement(TrustBadge, { badge: "L2_VALIDATED" }));
    expect(badge).toContain('data-trust="L2"');
    const tiers = renderToStaticMarkup(
      createElement(InsightTiersView, {
        insights: {
          tier1: { label: "spend_kg", value: 12 },
          tier3: ["What is total quantity moved?", "What is stock value?"],
        },
      }),
    );
    expect(tiers).toContain('data-testid="insight-tier1"');
    expect(tiers).toContain("spend_kg: 12");
    expect(tiers).toContain('data-testid="insight-tier3"');
    expect(tiers).toContain('data-executes="false"');
    expect(tiers).not.toContain('data-testid="insight-tier2"');
    expect(tiers).not.toContain("Comparison from executed SQL");
  });
});
