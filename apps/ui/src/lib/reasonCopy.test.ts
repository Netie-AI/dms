import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AnswerMessage } from "@/components/AnswerMessage";
import { AppProvider } from "@/context/AppContext";
import type { AnswerEnvelope } from "@/lib/types";
import {
  BADGE_SENTENCE,
  FALLBACK_REASON,
  REASON_COPY,
  plainReceiptWhy,
  plainShownReason,
} from "./reasonCopy";

function markup(envelope: AnswerEnvelope): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(AppProvider, null, createElement(AnswerMessage, { envelope })),
    ),
  );
}

function outside(html: string): string {
  const at = html.indexOf("<details");
  return at >= 0 ? html.slice(0, at) : html;
}

const base: AnswerEnvelope = {
  answer_id: "ans_plain",
  text: "Nothing to report.",
  values: [],
  badge: "ABSTAIN",
  abstained: true,
  assumptions: [],
  as_of: "2026-11-28T00:00:00Z",
  contributing_sources: [],
};

describe("reason copy", () => {
  it("gives every stored code a sentence that is not the code", () => {
    for (const [code, sentence] of Object.entries(REASON_COPY)) {
      expect(sentence, code).not.toContain(code);
      expect(plainShownReason(code), code).toBe(sentence);
      expect(plainShownReason(`GEN-01: ${code}`), code).toBe(sentence);
      expect(sentence).not.toContain("—");
    }
    expect(FALLBACK_REASON).not.toContain("plain-language");
  });

  it("turns a badge token into a sentence", () => {
    for (const [kind, sentence] of Object.entries(BADGE_SENTENCE)) {
      const line = plainReceiptWhy(`not abstained; badge=${kind}`);
      expect(line).toContain(sentence);
      expect(line).not.toContain(kind);
      expect(line).not.toContain("badge=");
    }
  });

  it("shows a governed answer in plain words", () => {
    const html = markup({
      ...base,
      badge: "L1_GOVERNED_METRIC",
      abstained: false,
      text: "Stock value is ready.",
      sql_used: "SELECT 1",
      audit_receipt: {
        include: {
          status: "rows",
          why: "executed result rows that produced the certified figures (query grain / LIMIT); not a claim of full-table completeness",
        },
        exclude: {
          status: "na",
          why: "N/A: no WHERE/HAVING/FILTER in executed SQL; none excluded at result grain",
        },
        unsure: { status: "none", why: "not abstained; badge=L1_GOVERNED_METRIC" },
      },
    });
    const open = outside(html);
    expect(open).toContain("governed");
    expect(open).toContain("⟨sql⟩");
    expect(open).toContain("Include / exclude / unsure");
    expect(open).toContain("It comes from a governed metric.");
    expect(open).toContain("The query did not filter rows.");
    expect(open).not.toContain("L1_GOVERNED_METRIC");
    expect(open).not.toContain("badge=");
    expect(html).not.toContain("not stamped");
    expect(html).not.toContain("Model not recorded");
    expect(html).not.toMatch(/<details[^>]*\sopen[\s>]/);
  });

  it("keeps a deadline code inside Details", () => {
    const html = markup({
      ...base,
      assumptions: ["GEN-01: serving_deadline_reserve"],
      audit_receipt: {
        include: { status: "na", why: "N/A: abstained; no include set" },
        exclude: { status: "na", why: "N/A: abstained; no exclude receipt" },
        unsure: { status: "abstain", why: "GEN-01: serving_deadline_reserve" },
      },
    });
    const open = outside(html);
    expect(open).toContain("This question waited so a running answer could finish.");
    expect(open).toContain("Nothing was included, because no answer was given.");
    expect(open).not.toContain("serving_deadline_reserve");
    expect(open).not.toContain("GEN-01");
    expect(open).not.toContain("No plain-language note is stored for this code.");
    expect(html.slice(html.indexOf("<details"))).toContain("serving_deadline_reserve");
    expect(html).not.toContain("not stamped");
    expect(html).not.toContain("Model not recorded");
  });

  it("shows an unknown measure as a sentence, with the token in Details", () => {
    const html = markup({
      ...base,
      assumptions: ["GEN-01: unknown_measure"],
    });
    const open = outside(html);
    expect(open).toContain("This question asks for a measure that is not defined here");
    expect(open).not.toContain("unknown_measure");
    expect(open).not.toContain("GEN-01");
    expect(html.slice(html.indexOf("<details"))).toContain("unknown_measure");
  });

  it("shows the model id only inside Details, and only when it was recorded", () => {
    const missing = markup({ ...base, badge: "L2_VALIDATED", abstained: false, served_model: "  " });
    expect(missing).not.toContain("Model not recorded");
    expect(missing).not.toContain("Answered by");
    const html = markup({
      ...base,
      badge: "L2_VALIDATED",
      abstained: false,
      served_model: "openai/gpt-oss-120b",
    });
    expect(outside(html)).not.toContain("openai/gpt-oss-120b");
    expect(html.slice(html.indexOf("<details"))).toContain("Answered by openai/gpt-oss-120b");
  });
});
