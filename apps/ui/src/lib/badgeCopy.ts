/** Badge chip copy — shared by Badge and vitest.
 *
 * Extracted so the wording is pinnable. The chip is the whole trust signal: it
 * is the one part of an answer a viewer reads before deciding whether to
 * believe the number next to it.
 */

import type { BadgeKind } from "@/lib/types";

export type BadgeTone = "ok" | "warn" | "mute";

export const BADGE_COPY: Record<BadgeKind, { label: string; tone: BadgeTone }> = {
  L0_CERTIFIED: { label: "certified", tone: "ok" },
  L1_GOVERNED_METRIC: { label: "governed", tone: "ok" },
  L2_VALIDATED: { label: "generated — check sources", tone: "warn" },
  L2_ANOMALOUS: { label: "unusual result — verify", tone: "warn" },
  // "abstain" was jargon wearing a grey chip, and on a projector a grey chip
  // reading "abstain" looks like the product failed. It did not fail: refusing
  // to answer rather than guessing is the thing being sold. The plain-language
  // framing already existed one click away in AnswerMessage ("No answer, on
  // purpose."); it belongs on the chip, where it is actually read.
  ABSTAIN: { label: "no answer — on purpose", tone: "mute" },
};

/** True when this badge asserts the number beside it can be relied on. */
export function isConfident(kind: BadgeKind): boolean {
  return BADGE_COPY[kind].tone === "ok";
}
