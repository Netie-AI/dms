import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AbstainNote,
  AnswerDetails,
  ServedModelLine,
  type StampEnvelope,
} from "./answerStamp";

function textOf(markup: string, testid: string): string {
  const match = markup.match(new RegExp(`data-testid="${testid}"[^>]*>([^<]*)<`));
  return match?.[1] ?? "";
}

function markup(envelope: StampEnvelope): string {
  return renderToStaticMarkup(
    createElement(
      "div",
      null,
      createElement(ServedModelLine, { envelope }),
      createElement(AbstainNote, { envelope }),
      createElement(AnswerDetails, { envelope }),
    ),
  );
}

describe("chat answer stamp", () => {
  it("shows the served model when the envelope has one", () => {
    const html = markup({
      badge: "L2_VALIDATED",
      abstained: false,
      served_model: "openai/gpt-oss-120b",
      assumptions: [],
    });
    expect(textOf(html, "served-model")).toBe("Answered by openai/gpt-oss-120b");
    expect(html).not.toContain("Model not recorded");
  });

  it("says Model not recorded when served_model is missing", () => {
    for (const served_model of [undefined, null, "", "   "]) {
      const html = markup({
        badge: "L2_VALIDATED",
        abstained: false,
        served_model,
        served_provider: "groq",
        served_attribution: "reported",
        assumptions: [],
      } as StampEnvelope);
      expect(textOf(html, "served-model")).toBe("Model not recorded");
      expect(html).not.toContain("groq");
      expect(html).not.toContain("Answered by");
    }
  });

  it("shows a known abstain reason in plain words and the raw code", () => {
    const html = markup({
      badge: "ABSTAIN",
      abstained: true,
      assumptions: ["GEN-01: insights_timeout", "live Cortex ask"],
    });
    expect(textOf(html, "abstain-label")).toBe(
      "The analysis took longer than the time limit, so no answer was given.",
    );
    expect(textOf(html, "abstain-code")).toBe("insights_timeout");
    expect(html).not.toContain('data-testid="abstain-sentence"');
  });

  it("shows an unknown abstain code as the label", () => {
    const html = markup({
      badge: "ABSTAIN",
      abstained: true,
      assumptions: ["GEN-01: ambiguous_measure:most_selling"],
    });
    expect(textOf(html, "abstain-label")).toBe("ambiguous_measure:most_selling");
    expect(textOf(html, "abstain-code")).toBe("ambiguous_measure:most_selling");
    expect(textOf(html, "abstain-sentence")).toBe(
      "No plain-language note is stored for this code.",
    );
    expect(textOf(html, "abstain-label")).not.toBe(
      "No plain-language note is stored for this code.",
    );
  });

  it("keeps assumptions collapsed under Details", () => {
    const html = markup({
      badge: "L0_CERTIFIED",
      abstained: false,
      served_model: "openai/gpt-oss-120b",
      assumptions: ["live Cortex ask", "include: sales"],
    });
    const openTag = html.match(/<details\b[^>]*>/)?.[0] ?? "";
    expect(openTag).toContain('data-testid="answer-details"');
    expect(openTag).not.toMatch(/\sopen(?:=|\s|>)/);
    expect(html).toContain(">Details<");
    expect(html).toContain("live Cortex ask");
    expect(html).toContain("include: sales");
    expect(html).not.toContain(">Assumptions<");
  });

  it("the chat card renders the stamp and the result panel", () => {
    const src = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "AnswerMessage.tsx"),
      "utf8",
    );
    expect(src).toContain("<ServedModelLine ");
    expect(src).toContain("<AbstainNote ");
    expect(src).toContain("<AnswerDetails ");
    expect(src).toContain("<ResultView ");
    expect(src).not.toContain("Assumptions");
  });
});
