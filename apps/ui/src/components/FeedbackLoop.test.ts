import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  DOWN_REASONS,
  FeedbackBar,
  FeedbackBarView,
  ReportProblem,
  fixBody,
  reviewBody,
  thumbBody,
} from "./FeedbackLoop";

describe("feedback loop controls", () => {
  it("hides the bar and the report button until the flag says on", () => {
    const bar = renderToStaticMarkup(
      createElement(FeedbackBar, {
        question: "how many widgets",
        sql: "SELECT qty FROM widgets",
        spaceId: "space-feedback-01",
        auditId: "",
      }),
    );
    const report = renderToStaticMarkup(createElement(ReportProblem));
    expect(bar).toBe("");
    expect(report).toBe("");
    expect(bar).not.toContain("Fix it");
    expect(report).not.toContain("Report a problem");
  });

  it("shows Yes, No, Fix it, and Request review when the view is enabled", () => {
    const markup = renderToStaticMarkup(
      createElement(FeedbackBarView, {
        question: "how many widgets",
        sql: "SELECT qty FROM widgets",
        spaceId: "space-feedback-01",
        auditId: "aud-1",
        initialPanel: "down",
      }),
    );
    expect(markup).toContain('data-testid="feedback-yes"');
    expect(markup).toContain('aria-label="Thumbs up"');
    expect(markup).toContain('data-testid="feedback-no"');
    expect(markup).toContain('aria-label="Thumbs down"');
    expect(markup).toContain("Fix it");
    expect(markup).toContain("Request review");
    for (const reason of DOWN_REASONS) {
      expect(markup).toContain(`data-testid="feedback-reason-${reason.id}"`);
      expect(markup).toContain(reason.label);
    }
  });

  it("builds a fix body from the edited SQL and does not invent a statement", () => {
    const body = fixBody({
      spaceId: "",
      question: "how many widgets",
      sql: "SELECT qty FROM widgets",
      interpretation: "count the widget rows",
    });
    expect(body).toEqual({
      space_id: "company-default",
      question: "how many widgets",
      sql: "SELECT qty FROM widgets",
      interpretation: "count the widget rows",
    });
    expect(thumbBody({
      spaceId: "s",
      question: "how many widgets",
      sql: "SELECT qty FROM widgets",
      auditId: "aud-1",
      vote: "down",
      reason: "wrong_number",
    }).vote).toBe("down");
    expect(reviewBody({
      spaceId: "s",
      question: "how many widgets",
      sql: "SELECT qty FROM widgets",
      interpretation: "",
      note: "",
    }).sql).toBe("SELECT qty FROM widgets");
  });
});
