import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AskStages, isDisabledStreamEvent, stageLabel, type AskStageEvent } from "./AskStages";

const stages: AskStageEvent[] = [
  { stage: "retrieve", t_ms: 400, status: "done" },
  { stage: "generate", t_ms: 2200, status: "started" },
  { stage: "retry", t_ms: 3100, status: "rung2" },
];

describe("AskStages", () => {
  it("renders the ladder with elapsed seconds and cancel", () => {
    const markup = renderToStaticMarkup(
      createElement(AskStages, {
        stages,
        onCancel: () => undefined,
      }),
    );
    expect(markup).toContain('data-testid="ask-stages"');
    expect(markup).toContain("Retrieve");
    expect(markup).toContain("Generate");
    expect(markup).toContain("Retry rung 2");
    expect(markup).toContain(">0s<");
    expect(markup).toContain(">2s<");
    expect(markup).toContain(">3s<");
    expect(markup).toContain('data-testid="ask-stages-cancel"');
    expect(markup).toContain(">Cancel<");
    expect(stageLabel(stages[2])).toBe("Retry rung 2");
  });

  it("hides cancel when the ask is finished and renders nothing when empty", () => {
    const done = renderToStaticMarkup(
      createElement(AskStages, { stages: [{ stage: "vote", t_ms: 1000, status: "done" }] }),
    );
    expect(done).toContain("Vote");
    expect(done).not.toContain("ask-stages-cancel");
    const empty = renderToStaticMarkup(createElement(AskStages, { stages: [] }));
    expect(empty).toBe("");
  });

  it("names the sell-profile steps and breathes on the open one", () => {
    const live: AskStageEvent[] = [
      { stage: "understand", t_ms: 20, status: "done" },
      { stage: "find", t_ms: 40, status: "done" },
      { stage: "write", t_ms: 80, status: "started" },
    ];
    const markup = renderToStaticMarkup(createElement(AskStages, { stages: live }));
    expect(markup).toContain("Understanding question");
    expect(markup).toContain("Finding tables");
    expect(markup).toContain("Writing SQL");
    expect(markup).toContain('data-testid="ask-breath"');
    expect(markup).toContain("ask-breath");
    expect(markup).toContain("motion-reduce:transition-none");
    expect(stageLabel({ stage: "run", t_ms: 1, status: "done" })).toBe("Running on database");
    expect(stageLabel({ stage: "check", t_ms: 1, status: "done" })).toBe("Checking");
    expect(stageLabel({ stage: "answer", t_ms: 1, status: "done" })).toBe("Answer");
  });

  it("treats a flag-off end event as disabled, not a stage", () => {
    expect(isDisabledStreamEvent({ enabled: false })).toBe(true);
    expect(isDisabledStreamEvent({ stage: "plan", t_ms: 1, status: "done" })).toBe(false);
    expect(isDisabledStreamEvent(null)).toBe(false);
  });
});
