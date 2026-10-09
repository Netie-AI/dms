import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ClarifyChoices } from "./ClarifyChoices";
import { clarifyPickPayload } from "@/lib/clarifyPick";

describe("clarify choices", () => {
  it("renders the question, option buttons, and a free-text field", () => {
    const html = renderToStaticMarkup(
      createElement(ClarifyChoices, {
        question: "Which measure?",
        options: [
          { id: "opt_a", label: "Alpha amount" },
          { id: "opt_b", label: "Beta amount" },
        ],
        onPick: () => undefined,
      }),
    );
    expect(html).toContain('data-testid="clarify-question"');
    expect(html).toContain("Which measure?");
    expect(html).toContain('data-testid="clarify-option-opt_a"');
    expect(html).toContain("Alpha amount");
    expect(html).toContain('data-testid="clarify-option-opt_b"');
    expect(html).toContain('data-testid="clarify-freetext"');
    expect(html).not.toContain("binding");
  });

  it("sends the original question and the option id, not a binding", () => {
    const payload = clarifyPickPayload(
      { original_question: "show both amounts", clarify_id: "clr_abc" },
      "opt_a",
    );
    expect(payload).toEqual({
      question: "show both amounts",
      clarify_id: "clr_abc",
      option_id: "opt_a",
      clarify_text: undefined,
    });
    expect(JSON.stringify(payload)).not.toContain("binding");
  });
});
