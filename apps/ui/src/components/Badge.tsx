import type { BadgeKind } from "@/lib/types";

const COPY: Record<BadgeKind, { label: string; tone: string }> = {
  L0_CERTIFIED: { label: "certified", tone: "ok" },
  L1_GOVERNED_METRIC: { label: "governed", tone: "ok" },
  L2_VALIDATED: { label: "generated — check sources", tone: "warn" },
  L2_ANOMALOUS: { label: "unusual result — verify", tone: "warn" },
  ABSTAIN: { label: "abstain", tone: "mute" },
};

export function Badge({ kind }: { kind: BadgeKind }) {
  const { label, tone } = COPY[kind];
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
