import { useState, type ReactNode } from "react";
import { Badge } from "@/components/Badge";
import { useApp } from "@/context/AppContext";
import type { AnswerEnvelope } from "@/lib/types";

/** Tokenize answer text so each values[].id becomes a button — no regex over prose. */
function renderWithValues(
  envelope: AnswerEnvelope,
  onValue: (id: string) => void,
): ReactNode[] {
  const parts: ReactNode[] = [];
  let remaining = envelope.text;
  const sorted = [...envelope.values].sort(
    (a, b) => String(b.value).length - String(a.value).length,
  );

  for (const v of sorted) {
    const formatted = new Intl.NumberFormat("en-MY", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(v.value);
    const candidates = [`RM ${formatted}`, formatted, String(v.value)];
    let hit: string | null = null;
    let idx = -1;
    for (const c of candidates) {
      const i = remaining.indexOf(c);
      if (i >= 0) {
        hit = c;
        idx = i;
        break;
      }
    }
    if (hit == null || idx < 0) continue;
    if (idx > 0) parts.push(remaining.slice(0, idx));
    parts.push(
      <button
        key={v.id}
        type="button"
        onClick={() => onValue(v.id)}
        className="mx-0.5 inline border-b-2 border-[var(--color-accent)] font-semibold text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]"
      >
        {hit}
      </button>,
    );
    remaining = remaining.slice(idx + hit.length);
  }
  if (remaining) parts.push(remaining);
  return parts.length ? parts : [envelope.text];
}

export function AnswerMessage({ envelope }: { envelope: AnswerEnvelope }) {
  const { selectValue } = useApp();
  const [showSql, setShowSql] = useState(false);

  return (
    <article className="border border-[var(--color-line)] bg-white/70 px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge kind={envelope.badge} />
        <button
          type="button"
          onClick={() => setShowSql((s) => !s)}
          className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
        >
          ⟨sql⟩
        </button>
        <span className="text-xs text-[var(--color-ink-muted)]">⟨lineage⟩</span>
      </div>
      <p className="text-[1.05rem] leading-relaxed text-[var(--color-ink)]">
        {renderWithValues(envelope, selectValue)}
      </p>
      {showSql && envelope.sql_used && (
        <pre className="mt-3 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-3 text-xs text-[var(--color-ink-muted)]">
          {envelope.sql_used}
        </pre>
      )}
      {envelope.assumptions.length > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Assumptions
          </p>
          <ul className="mt-1 list-inside list-disc text-sm text-[var(--color-ink-muted)]">
            {envelope.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}
