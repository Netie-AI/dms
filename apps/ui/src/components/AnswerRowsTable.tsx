import { useEffect, useRef, useState } from "react";
import { formatCellValue } from "@/lib/formatCellValue";
import { useIsLg } from "@/lib/viewport";

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
  const isLg = useIsLg();
  const scroller = useRef<HTMLDivElement>(null);
  const [moreRight, setMoreRight] = useState(false);

  // Below lg the table scrolls sideways inside its box; say so while columns hide.
  useEffect(() => {
    const el = scroller.current;
    if (!el || isLg) {
      setMoreRight(false);
      return;
    }
    const measure = () => setMoreRight(el.scrollWidth - el.clientWidth - el.scrollLeft > 1);
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    if (el.firstElementChild) ro?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", measure);
      ro?.disconnect();
    };
  }, [isLg, rows.length]);

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
  const isNumeric = (c: string) =>
    shown.every((r) => r[c] == null || typeof r[c] === "number") &&
    shown.some((r) => typeof r[c] === "number");
  // Phone only: numbers right-aligned and tabular; the first data column stays put.
  const numCls = (c: string) => (isNumeric(c) ? " max-lg:text-right max-lg:tabular-nums" : "");
  const stickyTh = "max-lg:sticky max-lg:left-0 max-lg:z-10 max-lg:border-r max-lg:border-r-[var(--color-line)] ";
  const stickyTd =
    "max-lg:sticky max-lg:left-0 max-lg:z-10 max-lg:border-r max-lg:border-r-[var(--color-line)] max-lg:bg-[var(--color-panel)] max-lg:group-odd:bg-[var(--color-surface)] ";

  return (
    <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-paper)]/50 lg:overflow-x-auto">
      <div className="relative">
      <div ref={scroller} data-testid="rows-scroller" className="max-lg:overflow-x-auto">
      <table data-testid="rows-table" className="min-w-full border-collapse text-left text-xs">
        <thead className="bg-[var(--color-paper-2)]">
          <tr>
            <th className="w-8 border-b border-[var(--color-line)] px-2 py-1.5 font-medium text-[var(--color-ink-muted)]">
              #
            </th>
            {cols.map((c, ci) => (
              <th
                key={c}
                className={`${ci === 0 ? stickyTh + "max-lg:bg-[var(--color-paper-2)] " : ""}border-b border-[var(--color-line)] px-2 py-1.5 font-medium text-[var(--color-ink-muted)]${numCls(c)}`}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} className="group odd:bg-[var(--color-surface)]/40">
              <td className="border-b border-[var(--color-line)]/50 px-2 py-1 tabular-nums text-[var(--color-ink-muted)]">
                {serverPaging ? pageOffset + i + 1 : i + 1}
              </td>
              {cols.map((c, ci) => (
                <td
                  key={c}
                  className={`${ci === 0 ? stickyTd : ""}border-b border-[var(--color-line)]/50 px-2 py-1 text-[var(--color-ink)]${numCls(c)}`}
                >
                  {formatCellValue(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {moreRight && !isLg && (
        <div
          data-testid="more-columns-cue"
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-[var(--color-ink)]/25 to-transparent"
        />
      )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--color-line)] px-2 py-1.5 text-xs text-[var(--color-ink-muted)] lg:text-[11px]">
        <p>
          {isLg ? "Showing " : "Rows "}
          {start}
          {isLg ? "-" : " to "}
          {end} of {total.toLocaleString()}
          {isLg ? ` row${total === 1 ? "" : "s"}` : ""}
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
              className="border border-[var(--color-line)] px-2 py-0.5 disabled:opacity-40 max-lg:min-h-11 max-lg:min-w-11"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={!canNext}
              onClick={() => onPageChange(pageOffset + size)}
              className="border border-[var(--color-line)] px-2 py-0.5 disabled:opacity-40 max-lg:min-h-11 max-lg:min-w-11"
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
