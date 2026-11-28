import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { publicTrustHint, trustCorpusRun, trustIsEmpty } from "./trustEmpty";
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
    expect(page).toContain("No benchmark run yet");
    expect(page).toContain("publicTrustHint(");
    expect(page).not.toContain("data.hint ??");
  });
});
