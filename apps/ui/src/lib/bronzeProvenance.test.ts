import { describe, expect, it } from "vitest";
import { bronzeWhenLabel } from "./bronzeProvenance";

describe("bronzeWhenLabel", () => {
  it("names a SQL extract", () => {
    expect(
      bronzeWhenLabel({
        source_kind: "sql",
        extracted_at: "2026-09-03T01:02:03.000000Z",
      }),
    ).toBe("extracted 2026-09-03T01:02:03.000000Z");
  });

  it("names a file upload, never an extract", () => {
    expect(
      bronzeWhenLabel({
        source_kind: "file",
        extracted_at: "2026-09-03T01:02:03.000000Z",
      }),
    ).toBe("uploaded 2026-09-03T01:02:03.000000Z");
  });

  it("does not invent a clock when the registry is empty", () => {
    expect(bronzeWhenLabel({ source_kind: "sql", extracted_at: null })).toBe(
      "no watermark recorded",
    );
  });
});
