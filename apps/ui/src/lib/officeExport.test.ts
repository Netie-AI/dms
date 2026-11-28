import { describe, expect, it } from "vitest";
import { docxBytes, pptxBytes, xlsxBytes } from "./officeExport";

const rows = [{ sku: "RS622KK", qty: 12.5 }];

function text(bytes: Uint8Array): string {
  return new TextDecoder().decode(bytes);
}

describe("office export copies the answer and adds no figure", () => {
  it("writes the row value into the workbook", () => {
    const body = text(xlsxBytes(rows));
    expect(body.startsWith("PK")).toBe(true);
    expect(body).toContain("RS622KK");
    expect(body).toContain(">12.5<");
    expect(body).not.toContain(">999<");
  });

  it("writes the answer text and the row into the document", () => {
    const body = text(docxBytes({ text: "Stock is 12.5", rows }));
    expect(body).toContain("Stock is 12.5");
    expect(body).toContain("RS622KK");
    expect(body).not.toContain("999");
  });

  it("writes the same text onto the slide", () => {
    const body = text(pptxBytes({ text: "Stock is 12.5", rows }));
    expect(body).toContain("Stock is 12.5");
    expect(body).toContain("12.5");
    expect(body).not.toContain("999");
  });
});
