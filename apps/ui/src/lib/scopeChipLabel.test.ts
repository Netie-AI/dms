import { describe, expect, it } from "vitest";
import { scopeChipLabel } from "./scopeChipLabel";

describe("scopeChipLabel", () => {
  it("shows live source count for active Space", () => {
    expect(scopeChipLabel("Finance", 3)).toBe("Asking: Finance · 3 sources");
    expect(scopeChipLabel("Finance", 1)).toBe("Asking: Finance · 1 source");
  });

  it("falls back to Company when no Space", () => {
    expect(scopeChipLabel(null, 0)).toBe("Asking: Company (default ACL)");
  });
});
