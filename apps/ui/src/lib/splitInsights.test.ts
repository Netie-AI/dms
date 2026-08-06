import { describe, expect, it } from "vitest";
import { splitInsights } from "./splitInsights";

describe("splitInsights", () => {
  it("splits Cortex INS-01 block into prose + bullets", () => {
    const text =
      "Top sales:\n  • SKU-A: 100\n\nInsights:\n- 2 row(s) in this result.\n- Highest qty: 100 (SKU-A).";
    const { prose, insights } = splitInsights(text);
    expect(prose).toContain("Top sales");
    expect(prose).not.toContain("Insights:");
    expect(insights).toEqual([
      "2 row(s) in this result.",
      "Highest qty: 100 (SKU-A).",
    ]);
  });

  it("leaves plain answers untouched", () => {
    const { prose, insights } = splitInsights("Total was 42.");
    expect(prose).toBe("Total was 42.");
    expect(insights).toEqual([]);
  });

  it("INS-03 live envelope text splits for AnswerMessage bullets", () => {
    const text =
      "Top sales by category:\n  • Electronics: 12\n  • Food: 5\n\n" +
      "Insights:\n- 2 row(s) in this result.\n" +
      "- Highest qty: 12 (Electronics).\n" +
      "- Lowest qty: 5 (Food).";
    const { prose, insights } = splitInsights(text);
    expect(prose).toContain("Electronics");
    expect(prose).not.toMatch(/Insights:/);
    expect(insights).toEqual([
      "2 row(s) in this result.",
      "Highest qty: 12 (Electronics).",
      "Lowest qty: 5 (Food).",
    ]);
  });
});
