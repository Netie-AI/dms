import type { ChartSpec } from "@/lib/types";

type Props = {
  chart: ChartSpec;
  rows: Record<string, unknown>[];
};

function num(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function label(v: unknown): string {
  return v == null ? "" : String(v);
}

function money(n: number): string {
  return new Intl.NumberFormat("en-MY", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(n);
}

/** Pure CSS/SVG bar charts — no chart library. */
export function SimpleChart({ chart, rows }: Props) {
  if (!rows.length) return null;
  const vals = rows.map((r) => num(r[chart.y]));
  const max = Math.max(...vals, 1);
  const title = chart.title ?? "Chart";

  if (chart.kind === "hbar") {
    return (
      <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
          {title}
        </p>
        <ul className="space-y-2">
          {rows.map((r, i) => {
            const y = num(r[chart.y]);
            const pct = Math.max(2, (y / max) * 100);
            return (
              <li key={`${label(r[chart.x])}-${i}`} className="grid grid-cols-[7rem_1fr_4.5rem] items-center gap-2 text-sm">
                <span className="truncate text-[var(--color-ink)]">{label(r[chart.x])}</span>
                <div className="h-3 overflow-hidden bg-[var(--color-paper-2)]">
                  <div
                    className="h-full bg-[var(--color-accent)] transition-[width] duration-500"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-right tabular-nums text-[var(--color-ink-muted)]">
                  {money(y)}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  // vertical bar
  const h = 120;
  const gap = 8;
  const barW = Math.min(48, Math.floor(280 / Math.max(rows.length, 1)));
  const width = rows.length * (barW + gap) + gap;

  return (
    <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        {title}
      </p>
      <svg
        viewBox={`0 0 ${width} ${h + 28}`}
        className="mx-auto block w-full max-w-md"
        role="img"
        aria-label={title}
      >
        {rows.map((r, i) => {
          const y = num(r[chart.y]);
          const bh = Math.max(2, (y / max) * h);
          const x = gap + i * (barW + gap);
          return (
            <g key={`${label(r[chart.x])}-${i}`}>
              <rect
                x={x}
                y={h - bh}
                width={barW}
                height={bh}
                fill="var(--color-accent)"
              />
              <text
                x={x + barW / 2}
                y={h + 14}
                textAnchor="middle"
                fontSize="10"
                fill="var(--color-ink-muted)"
              >
                {label(r[chart.x]).slice(0, 10)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
