import { describe, expect, it } from "vitest";
import {
  showSqlSource,
  showTruncatedFlag,
  watermarkTimeLabel,
} from "./previewWatermark";

describe("preview watermark header states", () => {
  it("SQL pull shows source, extracted time, and a visible cap flag", () => {
    const p = {
      source: "sqlserver://db.example.net:1433/sales#dbo.orders",
      extracted_at: "2026-09-03T05:35:00Z",
      truncated: true,
      source_kind: "sql",
    };
    const mark = watermarkTimeLabel(p);
    expect(mark.kind).toBe("extracted");
    expect(mark.text).toBe("extracted 2026-09-03T05:35:00Z");
    expect(mark.testId).toBe("preview-extracted-at");
    expect(showSqlSource(p)).toBe(true);
    expect(showTruncatedFlag(p)).toBe(true);
  });

  it("file upload is worded as uploaded, never extracted", () => {
    const p = {
      source: "sales.csv",
      extracted_at: "2026-09-03T05:35:00Z",
      truncated: false,
      source_kind: "file",
    };
    const mark = watermarkTimeLabel(p);
    expect(mark.kind).toBe("uploaded");
    expect(mark.text).toBe("uploaded 2026-09-03T05:35:00Z");
    expect(mark.text).not.toMatch(/extracted/);
    expect(mark.testId).toBe("preview-uploaded-at");
    expect(showSqlSource(p)).toBe(false);
    expect(showTruncatedFlag(p)).toBe(false);
  });

  it("missing registry row says no watermark recorded, never blank, never now", () => {
    const p = {
      source: null,
      extracted_at: null,
      truncated: null,
      source_kind: null,
    };
    const mark = watermarkTimeLabel(p);
    expect(mark.kind).toBe("missing");
    expect(mark.text).toBe("no watermark recorded");
    expect(mark.testId).toBe("preview-no-watermark");
    expect(showTruncatedFlag(p)).toBe(false);
  });
});
