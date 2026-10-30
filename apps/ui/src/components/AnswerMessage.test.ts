import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AnswerMessage, layerCopy } from "@/components/AnswerMessage";
import { AppProvider } from "@/context/AppContext";
import type { AnswerEnvelope } from "@/lib/types";

function markup(envelope: AnswerEnvelope): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(AppProvider, null, createElement(AnswerMessage, { envelope })),
    ),
  );
}

function envelope(reconfirm: AnswerEnvelope["reconfirm"]): AnswerEnvelope {
  return {
    answer_id: "ans_reconfirm",
    text: "The query needs a definition.",
    values: [],
    badge: "ABSTAIN",
    abstained: true,
    assumptions: [],
    as_of: "2026-10-28T00:00:00Z",
    contributing_sources: [],
    reconfirm,
  };
}

describe("Chat reconfirm card", () => {
  it("shows Yes without opening Details when a definition is present", () => {
    const html = markup(
      envelope({
        reason_code: "undeclared_join",
        reason_text: "That join is not in the approved ontology.",
        suggested_question: "",
        definition: "orders.customer_id = customers.id",
        token: "rcfexample",
      }),
    );
    const yesAt = html.indexOf('data-testid="chat-reconfirm-yes"');
    const detailsAt = html.indexOf("<details");
    expect(yesAt).toBeGreaterThan(-1);
    expect(detailsAt).toBeGreaterThan(yesAt);
    expect(html).toContain('data-testid="chat-reconfirm-no"');
    expect(html).toContain(">Yes<");
    expect(html).toContain(">No<");
    expect(html).toContain("That join is not in the approved ontology.");
    expect(html).toContain("orders.customer_id = customers.id");
    expect(html).not.toMatch(/<details[^>]*\sopen[\s>]/);
  });

  it("shows Yes for a suggestion when there is no definition", () => {
    const html = markup(
      envelope({
        reason_code: "unknown_measure",
        reason_text: "no measure matched",
        suggested_question: "closest stock question",
        token: "rcfexample",
      }),
    );
    const yesAt = html.indexOf('data-testid="chat-reconfirm-yes"');
    const detailsAt = html.indexOf("<details");
    const studioAt = html.indexOf('data-testid="studio-reconfirm-yes"');
    expect(yesAt).toBeGreaterThan(-1);
    expect(detailsAt).toBeGreaterThan(yesAt);
    expect(studioAt).toBeGreaterThan(detailsAt);
    expect(html).toContain('data-testid="studio-reconfirm-no"');
    expect(html).toContain("closest stock question");
    expect(html).toContain("no measure matched");
  });

  it("hides the card when the offer has no suggestion and no definition", () => {
    const html = markup(
      envelope({
        reason_code: "unknown_measure",
        reason_text: "no measure matched",
        suggested_question: "   ",
        token: "rcfexample",
      }),
    );
    expect(html).not.toContain('data-testid="chat-reconfirm-yes"');
  });
});

describe("Document answer layer copy", () => {
  const doc: AnswerEnvelope = {
    answer_id: "ans_doc",
    text: "Damaged goods may be returned with the original receipt.",
    values: [{ id: "v_count", value: 0, label: "row_count" }],
    badge: "L2_VALIDATED",
    abstained: false,
    sql_used: "-- document retrieval (no SQL)",
    assumptions: [],
    as_of: "2026-10-30T00:00:00Z",
    contributing_sources: [],
    route: "document",
  };

  it("says the documents answered, not generated SQL", () => {
    expect(layerCopy(doc).headline).toBe("Answered from this Space's documents.");
    expect(layerCopy(doc).headline).not.toMatch(/SQL/);
  });

  it("keeps the badge copy for every other route", () => {
    expect(layerCopy({ ...doc, route: "rag" }).headline).toBe(
      "This SQL was generated, then checked.",
    );
  });
});
