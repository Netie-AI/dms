import { useMemo, useRef, useState } from "react";
import type { PreviewSheet } from "@/lib/previewFixtures";

type Props = {
  sheet: PreviewSheet;
};

/**
 * Lightweight virtualized grid (windowed rows) — TanStack Virtual lands in U1 polish.
 * Contributing cells are highlighted; jump-to-next stepper included.
 */
export function PreviewGrid({ sheet }: Props) {
  const rows = useMemo(() => {
    const maxRow = Math.max(...sheet.cells.map((c) => c.row));
    const byRow = new Map<number, Map<string, (typeof sheet.cells)[0]>>();
    for (const cell of sheet.cells) {
      if (!byRow.has(cell.row)) byRow.set(cell.row, new Map());
      byRow.get(cell.row)!.set(cell.col, cell);
    }
    return Array.from({ length: maxRow }, (_, i) => {
      const row = i + 1;
      return { row, cols: byRow.get(row)! };
    });
  }, [sheet]);

  const contributingRowList = useMemo(
    () =>
      rows
        .filter((r) => [...r.cols.values()].some((c) => c.contributing))
        .map((r) => r.row),
    [rows],
  );

  const [cursor, setCursor] = useState(0);
  const [windowStart, setWindowStart] = useState(0);
  const rowHeight = 28;
  const visible = 12;
  const scroller = useRef<HTMLDivElement>(null);

  const jumpTo = (index: number) => {
    if (!contributingRowList.length) return;
    const i = ((index % contributingRowList.length) + contributingRowList.length) %
      contributingRowList.length;
    setCursor(i);
    const targetRow = contributingRowList[i];
    const start = Math.max(0, targetRow - 1 - 3);
    setWindowStart(start);
    scroller.current?.scrollTo({ top: start * rowHeight });
  };

  const slice = rows.slice(windowStart, windowStart + visible + 8);

  return (
    <div className="mt-2 border border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--color-line)] px-2 py-1.5">
        <p className="truncate text-xs font-medium text-[var(--color-ink)]">
          {sheet.title}
        </p>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="border border-[var(--color-line)] px-2 py-0.5 text-[11px]"
            onClick={() => jumpTo(cursor - 1)}
          >
            Prev
          </button>
          <span className="text-[11px] text-[var(--color-ink-muted)]">
            {contributingRowList.length
              ? `${cursor + 1}/${contributingRowList.length}`
              : "0"}
          </span>
          <button
            type="button"
            className="border border-[var(--color-line)] px-2 py-0.5 text-[11px]"
            onClick={() => jumpTo(cursor + 1)}
          >
            Next
          </button>
        </div>
      </div>
      <div
        ref={scroller}
        className="max-h-[336px] overflow-auto"
        onScroll={(e) => {
          const top = (e.target as HTMLDivElement).scrollTop;
          setWindowStart(Math.floor(top / rowHeight));
        }}
      >
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-[var(--color-paper-2)]">
            <tr>
              <th className="w-10 border-b border-[var(--color-line)] px-2 py-1 font-medium text-[var(--color-ink-muted)]">
                #
              </th>
              {sheet.columns.map((c) => (
                <th
                  key={c}
                  className="border-b border-[var(--color-line)] px-2 py-1 font-medium text-[var(--color-ink-muted)]"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {windowStart > 0 && (
              <tr style={{ height: windowStart * rowHeight }}>
                <td colSpan={sheet.columns.length + 1} />
              </tr>
            )}
            {slice.map(({ row, cols }) => (
              <tr key={row} style={{ height: rowHeight }}>
                <td className="border-b border-[var(--color-line)]/60 px-2 text-[var(--color-ink-muted)]">
                  {row}
                </td>
                {sheet.columns.map((col) => {
                  const cell = cols.get(col);
                  const hot = cell?.contributing;
                  return (
                    <td
                      key={col}
                      className={`border-b border-[var(--color-line)]/60 px-2 ${
                        hot
                          ? "bg-[var(--color-accent-soft)] font-semibold text-[var(--color-accent)]"
                          : "text-[var(--color-ink-muted)] opacity-50"
                      }`}
                    >
                      {cell?.value ?? ""}
                    </td>
                  );
                })}
              </tr>
            ))}
            {windowStart + slice.length < rows.length && (
              <tr
                style={{
                  height: (rows.length - windowStart - slice.length) * rowHeight,
                }}
              >
                <td colSpan={sheet.columns.length + 1} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
