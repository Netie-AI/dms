/** Copy for SQL vs file bronze watermarks. Derive kind on the server, not here. */

export type BronzeProvenance = {
  source?: string | null;
  source_kind?: string | null;
  extracted_at?: string | null;
  truncated?: boolean | null;
};

export function bronzeWhenLabel(p: BronzeProvenance): string {
  if (p.extracted_at == null || p.extracted_at === "") {
    return "no watermark recorded";
  }
  if (p.source_kind === "file") {
    return `uploaded ${p.extracted_at}`;
  }
  return `extracted ${p.extracted_at}`;
}
