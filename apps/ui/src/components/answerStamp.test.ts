import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AbstainNote, AnswerDetails, type StampEnvelope } from "./answerStamp";

function textOf(markup: string, testid: string): string {
  const match = markup.match(new RegExp(`data-testid="${testid}"[^>]*>([^<]*)<`));
  return match?.[1] ?? "";
}

function markup(envelope: StampEnvelope): string {
  return renderToStaticMarkup(
    createElement(
      "div",
      null,
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
    const detailsAt = html.indexOf("<details");
    expect(detailsAt).toBeGreaterThan(-1);
    expect(html.slice(0, detailsAt)).not.toContain("openai/gpt-oss-120b");
  });

  it("hides the model line when served_model is missing", () => {
    for (const served_model of [undefined, null, "", "   "]) {
      const html = markup({
        badge: "L2_VALIDATED",
        abstained: false,
        served_model,
        served_provider: "groq",
        served_attribution: "reported",
        assumptions: [],
      } as StampEnvelope);
      expect(html).not.toContain('data-testid="served-model"');
      expect(html).not.toContain("Model not recorded");
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
      "The analysis took longer than the time limit, so no figure was given.",
    );
    expect(textOf(html, "abstain-code")).toBe("insights_timeout");
    const detailsAt = html.indexOf("<details");
    expect(html.slice(0, detailsAt)).not.toContain("insights_timeout");
    expect(html).not.toContain("No plain-language note is stored for this code.");
  });

  it("maps an unknown-looking code to its sentence and keeps the token in Details", () => {
    const html = markup({
      badge: "ABSTAIN",
      abstained: true,
      assumptions: ["GEN-01: ambiguous_measure:most_selling"],
    });
    expect(textOf(html, "abstain-label")).toBe(
      "More than one measure fits, so no figure was given until you choose.",
    );
    expect(textOf(html, "abstain-code")).toBe("ambiguous_measure:most_selling");
    const detailsAt = html.indexOf("<details");
    expect(html.slice(0, detailsAt)).not.toContain("ambiguous_measure");
    expect(html).not.toContain("No plain-language note is stored for this code.");
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
    expect(src).not.toContain("<ServedModelLine ");
    expect(src).not.toContain("Model not recorded");
    expect(src).toContain("<AbstainNote ");
    expect(src).toContain("<AnswerDetails ");
    expect(src).toContain("<ResultView ");
    expect(src).not.toContain("Assumptions");
    const stamp = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "answerStamp.tsx"),
      "utf8",
    );
    expect(stamp).toContain("<ServedModelLine ");
    expect(stamp).toContain('data-testid="abstain-code"');
  });
});
