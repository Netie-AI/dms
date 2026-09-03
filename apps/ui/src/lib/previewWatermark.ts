export type PreviewWatermark = {
  source?: string | null;
  extracted_at?: string | null;
  truncated?: boolean | null;
  source_kind?: string | null;
};

export type WatermarkTime = {
  kind: "extracted" | "uploaded" | "missing";
  text: string;
  testId: "preview-extracted-at" | "preview-uploaded-at" | "preview-no-watermark";
};

/** Library preview header copy. Missing is never blank and never "now". */
export function watermarkTimeLabel(p: PreviewWatermark): WatermarkTime {
  if (p.extracted_at == null || p.extracted_at === "") {
    return {
      kind: "missing",
      text: "no watermark recorded",
      testId: "preview-no-watermark",
    };
  }
  if (p.source_kind === "file") {
    return {
      kind: "uploaded",
      text: `uploaded ${p.extracted_at}`,
      testId: "preview-uploaded-at",
    };
  }
  return {
    kind: "extracted",
    text: `extracted ${p.extracted_at}`,
    testId: "preview-extracted-at",
  };
}

export function showTruncatedFlag(p: PreviewWatermark): boolean {
  return p.truncated === true;
}

export function showSqlSource(p: PreviewWatermark): boolean {
  return p.source_kind === "sql" && Boolean(p.source);
}
