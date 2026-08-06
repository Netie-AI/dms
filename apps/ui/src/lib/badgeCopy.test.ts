import { describe, expect, it } from "vitest";
import { BADGE_COPY, isConfident } from "./badgeCopy";
import type { BadgeKind } from "@/lib/types";

const ALL: BadgeKind[] = [
  "L0_CERTIFIED",
  "L1_GOVERNED_METRIC",
  "L2_VALIDATED",
  "L2_ANOMALOUS",
  "ABSTAIN",
];

describe("badge copy", () => {
  it("covers every badge the envelope can carry", () => {
    for (const kind of ALL) {
      expect(BADGE_COPY[kind]?.label, `${kind} has no copy`).toBeTruthy();
    }
  });

  it("does not label an abstention with bare jargon", () => {
    // A grey chip reading "abstain" reads, on a projector, as the product
    // breaking. Abstaining is the product working, and the chip has to say so
    // without needing a click to explain it.
    const label = BADGE_COPY.ABSTAIN.label;
    expect(label).not.toBe("abstain");
    expect(label.toLowerCase()).toContain("on purpose");
  });

  it("never dresses an abstention as confidence", () => {
    expect(isConfident("ABSTAIN")).toBe(false);
    expect(BADGE_COPY.ABSTAIN.tone).not.toBe("ok");
  });

  it("keeps generated answers visibly weaker than governed ones", () => {
    // L2 is model-written SQL. It must not read as trustworthy as a human
    // reviewed metric, or the ladder stops meaning anything.
    expect(isConfident("L0_CERTIFIED")).toBe(true);
    expect(isConfident("L1_GOVERNED_METRIC")).toBe(true);
    expect(isConfident("L2_VALIDATED")).toBe(false);
    expect(isConfident("L2_ANOMALOUS")).toBe(false);
    expect(BADGE_COPY.L2_VALIDATED.label).toMatch(/check sources/i);
  });

  it("gives every badge a distinct label", () => {
    const labels = ALL.map((k) => BADGE_COPY[k].label);
    expect(new Set(labels).size).toBe(labels.length);
  });
});
