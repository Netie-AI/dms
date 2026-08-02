import { formatCellValue } from "@/lib/formatCellValue";

type Props = {
  rows: Record<string, unknown>[];
  /** Client-only cap when server paging is not used. */
  maxRows?: number;
  /** Server total row count (warehouse/bronze table size). */
  totalRows?: number;
  pageOffset?: number;
  pageSize?: number;
  onPageChange?: (offset: number) => void;
};

/** Ranked / tabular answer body — architecture wants numbers you can scan, not only prose. */
export function AnswerRowsTable({
  rows,
  maxRows = 25,
  totalRows,
  pageOffset = 0,
  pageSize,
  onPageChange,
}: Props) {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]);
  const serverPaging = totalRows != null && pageSize != null && onPageChange != null;
  const shown = serverPaging ? rows : rows.slice(0, maxRows);
  const total = serverPaging ? totalRows : rows.length;
  const size = serverPaging ? pageSize : maxRows;
  const start = serverPaging ? pageOffset + 1 : 1;
  const end = serverPaging ? pageOffset + shown.length : Math.min(maxRows, rows.length);
  const canPrev = serverPaging && pageOffset > 0;
  const canNext = serverPaging && pageOffset + shown.length < total;

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
                {serverPaging ? pageOffset + i + 1 : i + 1}
              </td>
              {cols.map((c) => (
                <td
                  key={c}
                  className="border-b border-[var(--color-line)]/50 px-2 py-1 text-[var(--color-ink)]"
                >
                  {formatCellValue(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--color-line)] px-2 py-1.5 text-[11px] text-[var(--color-ink-muted)]">
        <p>
          Showing {start}-{end} of {total.toLocaleString()} row{total === 1 ? "" : "s"}
          {!serverPaging && rows.length > maxRows
            ? " — Download CSV for the full set."
            : ""}
        </p>
        {serverPaging && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={!canPrev}
              onClick={() => onPageChange(Math.max(0, pageOffset - size))}
              className="border border-[var(--color-line)] px-2 py-0.5 disabled:opacity-40"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={!canNext}
              onClick={() => onPageChange(pageOffset + size)}
              className="border border-[var(--color-line)] px-2 py-0.5 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function hasTabularRows(envelope: { rows?: Record<string, unknown>[] | null }): boolean {
  return (envelope.rows?.length ?? 0) > 0;
}
