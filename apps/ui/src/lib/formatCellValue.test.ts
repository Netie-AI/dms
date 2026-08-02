import { describe, expect, it } from "vitest";
import { formatCellValue } from "./formatCellValue";

describe("formatCellValue", () => {
  it("renders _src STRUCT[] without [object Object]", () => {
    const out = formatCellValue([{ ref_id: "src_q3", row: 12 }]);
    expect(out).not.toContain("[object Object]");
    expect(out).toContain("src_q3");
    expect(out).toContain("12");
  });

  it("stringifies nested objects readably", () => {
    const out = formatCellValue({ a: 1, b: "x" });
    expect(out).not.toContain("[object Object]");
    expect(out).toContain('"a":1');
  });
});
