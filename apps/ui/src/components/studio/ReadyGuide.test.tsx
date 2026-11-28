import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReadyGuide, type ReadyGuideBody } from "./ReadyGuide";

const guide: ReadyGuideBody = {
  steps: [
    {
      n: 1,
      id: "company",
      title: "Create the company",
      proves: "A tenant and a Space exist, and the call did not fail.",
      done: true,
    },
    {
      n: 2,
      id: "connect",
      title: "Connect a source",
      proves: "The source listed the tables the login can read.",
      done: false,
    },
  ],
  progress: { done: 1, total: 5 },
  demo: false,
  demo_enabled: true,
  questions: ["How many orders are there?", "Which products have no orders?", "Who bought a bolt?", "How many customers are there?", "Which sku is on more than one order?"],
  questions_status: "ok",
};

describe("ReadyGuide", () => {
  it("renders nothing when the route was not loaded", () => {
    const html = renderToStaticMarkup(createElement(ReadyGuide, { initial: null }));
    expect(html).toBe("");
  });

  it("shows numbered steps, progress, the proof, and five questions", () => {
    const html = renderToStaticMarkup(createElement(ReadyGuide, { initial: guide }));
    expect(html).toContain('data-testid="ready-guide"');
    expect(html).toContain("Step 1 of 5 done");
    expect(html).toContain("1. Create the company");
    expect(html).toContain("Proves: A tenant and a Space exist");
    expect(html).toContain('data-testid="ready-questions"');
    expect(html).toContain("Try these 5 questions");
    expect(html).toContain("How many orders are there?");
    expect(html).toContain('data-testid="ready-demo"');
  });
});
