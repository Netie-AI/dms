import { describe, expect, it } from "vitest";
import { SIGN_INTENT, strokesToSvg } from "./signCapture";

describe("sign capture", () => {
  it("uses the consent sentence", () => {
    expect(SIGN_INTENT).toBe("I agree to sign electronically");
  });

  it("turns strokes into an svg path", () => {
    const svg = strokesToSvg(
      [
        [
          { x: 1, y: 2 },
          { x: 3, y: 4 },
        ],
      ],
      80,
      40,
    );
    expect(svg).toContain('d="M 1.0 2.0 L 3.0 4.0"');
    expect(svg.startsWith("<svg")).toBe(true);
  });
});
