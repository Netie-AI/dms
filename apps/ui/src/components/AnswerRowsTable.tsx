import type { AnswerEnvelope } from "@/lib/types";

type Props = {
  rows: Record<string, unknown>[];
  maxRows?: number;
};

function cell(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "number") {
    return Number.isFinite(v)
      ? v.toLocaleString("en-MY", { maximumFractionDigits: 4 })
      : String(v);
  }
  return String(v);
}

/** Ranked / tabular answer body — architecture wants numbers you can scan, not only prose. */
export function AnswerRowsTable({ rows, maxRows = 25 }: Props) {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]);
  const shown = rows.slice(0, maxRows);

  return (
    <div className="mt-4 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)]/50">
      <table className="min-w-full border-collapse text-left text-xs">
        <thead className="bg-[var(--color-paper-2)]">
          <tr>
            <th className="w-8 border-b border-[var(--color-line)] px-2 py-1.5 font-medium text-[var(--color-ink-muted)]">
              #
            </th>
            {cols.map((c) => (
              <th
                key={c}
                className="border-b border-[var(--color-line)] px-2 py-1.5 font-medium text-[var(--color-ink-muted)]"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} className="odd:bg-[var(--color-surface)]/40">
              <td className="border-b border-[var(--color-line)]/50 px-2 py-1 tabular-nums text-[var(--color-ink-muted)]">
                {i + 1}
              </td>
              {cols.map((c) => (
                <td
                  key={c}
                  className="border-b border-[var(--color-line)]/50 px-2 py-1 text-[var(--color-ink)]"
                >
                  {cell(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && (
        <p className="border-t border-[var(--color-line)] px-2 py-1.5 text-[11px] text-[var(--color-ink-muted)]">
          Showing {maxRows} of {rows.length} rows — Download CSV for the full set.
        </p>
      )}
    </div>
  );
}

export function hasTabularRows(envelope: AnswerEnvelope): boolean {
  return (envelope.rows?.length ?? 0) > 0;
}
