import { readStamp, stampSlotsFor } from "./stamps";

type Props = {
  envelope: object;
};

/** Read-only stamps. Absent keys say "not stamped". No provider picker. */
export function StampsPanel({ envelope }: Props) {
  const slots = stampSlotsFor(envelope);
  return (
    <section
      data-testid="studio-stamps"
      className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        Stamps
      </p>
      <dl className="mt-2 grid gap-1 text-xs">
        {slots.map((slot) => (
          <div key={slot.id} className="grid grid-cols-[11rem_1fr] gap-2">
            <dt className="text-[var(--color-ink-muted)]">{slot.label}</dt>
            <dd data-stamp={slot.id} className="break-all font-mono text-[var(--color-ink)]">
              {readStamp(envelope, slot)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
