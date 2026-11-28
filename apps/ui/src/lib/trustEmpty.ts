import type { TrustRun } from "./types";

/** A Cortex 404 body is JSON. The page shows a sentence instead. */
export function publicTrustHint(hint: string | undefined | null): string {
  const text = (hint ?? "").trim();
  if (!text || text.startsWith("{") || text.startsWith("[")) {
    return "The evidence file is not on disk yet.";
  }
  return text;
}

/** grade52 is the corpus tile. Any other present file fills the tile when that one is absent. */
export function trustCorpusRun(
  runs: Record<string, TrustRun> | undefined,
): TrustRun | undefined {
  if (!runs) return undefined;
  if (runs.corpus?.present) return runs.corpus;
  return Object.values(runs).find((run) => run.present);
}

export function trustIsEmpty(data: { empty?: boolean } | null | undefined): boolean {
  return data?.empty === true;
}

/** A missing file, run, or field. No digit, so a gap cannot read as a clean zero. */
export const NOT_MEASURED = "Not measured yet";

/** A count only when the artifact stored a finite number. Missing is not zero. */
export function recordedCount(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

export function measuredText(value: unknown): string {
  const n = recordedCount(value);
  return n === null ? NOT_MEASURED : String(n);
}

/** The wrong pill. A missing WRONG is the unmeasured sentence, never "0 wrong". */
export function wrongCountLabel(value: unknown): string {
  const n = recordedCount(value);
  return n === null ? NOT_MEASURED : `${n} wrong`;
}

/** The claim line. The "0 confidently wrong" bar is shown only after WRONG was stored. */
export function claimHeadline(
  claim: { statement?: string; confidently_wrong?: unknown } | null | undefined,
): string {
  if (recordedCount(claim?.confidently_wrong) === null) return NOT_MEASURED;
  const statement = (claim?.statement ?? "").trim();
  return statement || NOT_MEASURED;
}
