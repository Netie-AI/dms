/*
 * Words the Studio receipt adds for a phone (PHONE-PATH-01, #513). Pure, so the wording is testable
 * without a browser. The API's own text is shown as it came, apart from what would mislead a reader:
 * a second full stop, and characters that reorder the text around them.
 */

// Bidirectional overrides and isolates (U+202A to U+202E, U+2066 to U+2069) can flip the words that follow.
const BIDI = /[‪-‮⁦-⁩]/g;

export type ReceiptVisibility = {
  ingested: number;
  serving_sync_detail?: string | null;
};

/** The one sentence that tells a phone user whether Chat can see the upload (shown when the API says it cannot confirm). */
export function chatVisibilityLine(r: ReceiptVisibility): string {
  if (r.ingested === 0) return "Nothing was ingested, so Chat cannot see this upload.";
  const detail = (r.serving_sync_detail ?? "").replace(BIDI, "").replace(/[.\s]+$/, "");
  // A detail that already ends in "!", "?" or an ellipsis keeps its own mark: no "!." or "....".
  const closed = /[!?…]$/.test(detail);
  return `Not confirmed that Chat can see this upload${detail ? `: ${detail}` : ""}${closed ? "" : "."}`;
}

export type ReceiptFile = { table?: string | null; ingested?: boolean | null; reason?: string | null; fix?: string | null };

/** A row is "not ingested" when the API says so; with no flag, when it names no table. A landed row is never called refused. */
export function isRefused(f: ReceiptFile): boolean {
  return f.ingested === false || (f.ingested == null && !f.table);
}
