import { describe, expect, it } from "vitest";
import {
  columnOrder,
  csvDownloadName,
  csvEscape,
  isSummaryExport,
  rowsToCsv,
} from "./rowsToCsv";

describe("rowsToCsv", () => {
  it("is byte-stable: same rows produce the same string twice", () => {
    const rows = [
      { sku: "SKU-BETA", revenue_myr: 1545366.4 },
      { sku: "SKU-ALPHA", revenue_myr: 12 },
    ];
    expect(rowsToCsv(rows)).toBe(rowsToCsv(rows));
  });

  it("writes UTF-8 BOM, CRLF, and raw numbers (no locale, no clock)", () => {
    const csv = rowsToCsv([{ sku: "A", qty: 1000 }]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toBe("\uFEFFsku,qty\r\nA,1000\r\n");
    expect(csv).not.toContain("1,000");
    expect(csv).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
  });

  it("quotes commas, quotes, and newlines per RFC 4180", () => {
    const csv = rowsToCsv([{ note: 'he said "hi, there"\nnext' }]);
    expect(csv).toBe('\uFEFFnote\r\n"he said ""hi, there""\nnext"\r\n');
  });

  it("keeps Malay / UTF-8 headers intact", () => {
    const csv = rowsToCsv([{ Kuantiti: 2, Negeri: "Kuala Lumpur" }]);
    expect(csv).toContain("Kuantiti");
    expect(csv).toContain("Kuala Lumpur");
  });

  it("unions columns in first-seen order, not first-row-only", () => {
    expect(columnOrder([{ a: 1 }, { b: 2, a: 3 }])).toEqual(["a", "b"]);
    const csv = rowsToCsv([{ a: 1 }, { b: 2, a: 3 }]);
    expect(csv).toBe("\uFEFFa,b\r\n1,\r\n3,2\r\n");
  });

  it("renders _src structs without [object Object]", () => {
    const csv = rowsToCsv([{ sku: "A", _src: [{ ref_id: "src_q3", row: 12 }] }]);
    expect(csv).toContain("src_q3:12");
    expect(csv).not.toContain("[object Object]");
  });

  it("returns BOM only for an empty row list", () => {
    expect(rowsToCsv([])).toBe("\uFEFF");
  });
});

describe("csvDownloadName", () => {
  it("names the file from answer_id, never the clock", () => {
    expect(csvDownloadName("ans_live_9f3c")).toBe("dms_answer_ans_live_9f3c.csv");
    expect(csvDownloadName("ans/live\\9f3c")).toBe("dms_answer_ans_live_9f3c.csv");
    expect(csvDownloadName("")).toBe("dms_answer_export.csv");
    expect(csvDownloadName("../x")).toBe("dms_answer_x.csv");
  });
});

describe("csvEscape", () => {
  it("leaves plain tokens unquoted", () => {
    expect(csvEscape("SKU-BETA")).toBe("SKU-BETA");
  });
});

describe("isSummaryExport", () => {
  it("detects a one-cell aggregate so download can fetch drill rows", () => {
    expect(isSummaryExport([{ total_revenue: 10 }])).toBe(true);
    expect(isSummaryExport([{ sku: "A", revenue_myr: 10 }])).toBe(false);
    expect(isSummaryExport([])).toBe(false);
  });
});
