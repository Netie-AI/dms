import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SignPanel } from "@/pages/SignPanel";

describe("sign panel", () => {
  it("renders nothing when signing is off", () => {
    const html = renderToStaticMarkup(createElement(SignPanel, { enabled: false }));
    expect(html).toBe("");
  });

  it("shows the consent sentence when signing is on", () => {
    const html = renderToStaticMarkup(createElement(SignPanel, { enabled: true }));
    expect(html).toContain("I agree to sign electronically");
    expect(html).toContain("data-testid=\"sign-panel\"");
    expect(html).toContain("Typed signature");
  });
});
