import type { BadgeKind } from "@/lib/types";
import { BADGE_COPY } from "@/lib/badgeCopy";

export function Badge({ kind }: { kind: BadgeKind }) {
  const { label, tone } = BADGE_COPY[kind];
  const color =
    tone === "ok"
      ? "text-[var(--color-badge-ok)] bg-[var(--color-accent-soft)]"
      : tone === "warn"
        ? "text-[var(--color-badge-warn)] bg-[var(--color-warn-soft)]"
        : "text-[var(--color-badge-mute)] bg-[var(--color-paper-2)]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium tracking-wide ${color}`}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-sm bg-current" />
      {label}
    </span>
  );
}
