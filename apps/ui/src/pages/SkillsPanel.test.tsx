import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SkillsPanel } from "@/pages/SkillsPanel";

const rows = [
  {
    name: "google-ai-insights",
    description: "Cite a Google AI Overview, then infer from governed data.",
  },
];

describe("skills panel", () => {
  it("renders nothing when the flag is off", () => {
    const html = renderToStaticMarkup(
      createElement(SkillsPanel, {
        enabled: false,
        skills: rows,
        googleMessage: "off",
        query: "",
        onQuery: () => undefined,
      }),
    );
    expect(html).toBe("");
  });

  it("shows search and the google message when the flag is on", () => {
    const html = renderToStaticMarkup(
      createElement(SkillsPanel, {
        enabled: true,
        skills: rows,
        googleMessage: "Google AI insights is off. DMS_WEB_INSIGHT is not the digit 1.",
        query: "",
        onQuery: () => undefined,
      }),
    );
    expect(html).toContain('data-testid="skills-panel"');
    expect(html).toContain('data-testid="skills-search"');
    expect(html).toContain("google-ai-insights");
    expect(html).toContain("DMS_WEB_INSIGHT is not the digit 1");
  });
});
