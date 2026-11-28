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
