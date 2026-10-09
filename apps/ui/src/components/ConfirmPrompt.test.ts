import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ConfirmPrompt } from "./ConfirmPrompt";

describe("confirm prompt", () => {
  it("renders the reason, the suggestion, and Yes/No", () => {
    const html = renderToStaticMarkup(
      createElement(ConfirmPrompt, {
        reason: "I could not total that column.",
        suggestion: "What is the total amount?",
        onChoice: () => undefined,
      }),
    );
    expect(html).toContain('data-testid="confirm-reason"');
    expect(html).toContain("I could not total that column.");
    expect(html).toContain('data-testid="confirm-suggestion"');
    expect(html).toContain("What is the total amount?");
    expect(html).toContain('data-testid="confirm-yes"');
    expect(html).toContain('data-testid="confirm-no"');
    expect(html).toContain(">Yes<");
    expect(html).toContain(">No<");
  });
});
