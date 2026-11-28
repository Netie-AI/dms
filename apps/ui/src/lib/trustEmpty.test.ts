import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  claimHeadline,
  measuredText,
  publicTrustHint,
  trustCorpusRun,
  trustIsEmpty,
  wrongCountLabel,
} from "./trustEmpty";
import type { TrustRun } from "./types";

const here = dirname(fileURLToPath(import.meta.url));

function run(id: string, present = true): TrustRun {
  return {
    id,
    label: id,
    proves: "file",
    present,
    file: `${id}.json`,
    totals: { total: 4, correct: 2, wrong: 1, abstain: 1 },
    rates: { correct: 0.5, abstain: 0.25 },
  };
}

describe("trust empty state", () => {
  it("hides a raw JSON hint and keeps a sentence", () => {
    expect(publicTrustHint('{"detail":"Not Found"}')).toBe(
      "The evidence file is not on disk yet.",
    );
    expect(publicTrustHint("  [1]")).toBe("The evidence file is not on disk yet.");
    expect(publicTrustHint("Set CORTEX_API_KEY")).toBe("Set CORTEX_API_KEY");
    expect(publicTrustHint("")).toBe("The evidence file is not on disk yet.");
  });

  it("uses the corpus file, then any other present run", () => {
    expect(trustCorpusRun(undefined)).toBeUndefined();
    expect(trustCorpusRun({ score_hostile: run("score_hostile") })?.id).toBe("score_hostile");
    expect(
      trustCorpusRun({ score_hostile: run("score_hostile"), corpus: run("corpus") })?.id,
    ).toBe("corpus");
    expect(trustIsEmpty({ empty: true })).toBe(true);
    expect(trustIsEmpty({ empty: false })).toBe(false);
    expect(trustIsEmpty(null)).toBe(false);
  });

  it("the page shows the empty title and does not print the raw hint", () => {
    const page = readFileSync(join(here, "../pages/TrustPage.tsx"), "utf8");
    expect(page).toContain('data-testid="trust-empty"');
    expect(page).toContain('data-testid="trust-unmeasured"');
    expect(page).toContain("No benchmark run yet");
    expect(page).toContain("Not measured yet");
    expect(page).toContain("publicTrustHint(");
    expect(page).toContain("wrongCountLabel(");
    expect(page).not.toContain("data.hint ??");
    expect(page).not.toContain("0 wrong");
    expect(page).not.toContain("totals.wrong ?? 0");
  });

  it("a missing WRONG renders unmeasured and never as 0", () => {
    for (const missing of [undefined, null, "0", Number.NaN]) {
      const label = wrongCountLabel(missing);
      const markup = renderToStaticMarkup(createElement("span", null, label));
      expect(label).toBe("Not measured yet");
      expect(markup).toBe("<span>Not measured yet</span>");
      expect(markup).not.toMatch(/0/);
    }
    expect(wrongCountLabel(0)).toBe("0 wrong");
    expect(wrongCountLabel(2)).toBe("2 wrong");
    expect(measuredText(undefined)).toBe("Not measured yet");
    expect(measuredText(0)).toBe("0");
    expect(claimHeadline({})).toBe("Not measured yet");
    expect(claimHeadline({})).not.toMatch(/0/);
    expect(claimHeadline({ confidently_wrong: 0, statement: "0 confidently wrong" })).toBe(
      "0 confidently wrong",
    );
  });
});
