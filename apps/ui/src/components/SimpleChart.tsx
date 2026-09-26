import type { ReactNode } from "react";
import { pieSlices, scatterPoints } from "@/lib/chartSpec";
import type { ChartSpec } from "@/lib/types";

/** Categorical slots 1–6 in fixed order (never cycled; pie caps at 6 slices). */
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];

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

function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  // Angles from 12 o'clock, clockwise. A full circle needs two half arcs.
  if (a1 - a0 >= 2 * Math.PI - 1e-9) {
    return `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx} ${cy + r} A ${r} ${r} 0 1 1 ${cx} ${cy - r} Z`;
  }
  const p = (a: number) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const [x0, y0] = p(a0);
  const [x1, y1] = p(a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`;
}

function pct(share: number): string {
  return new Intl.NumberFormat("en-MY", { style: "percent", maximumFractionDigits: 1 }).format(share);
}

function Frame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        {title}
      </p>
      {children}
    </div>
  );
}

/** Pure CSS/SVG charts — no chart library. Values come from ``rows``; the
 *  envelope's ``vega_lite`` spec is the portable artifact for BI / export. */
export function SimpleChart({ chart, rows }: Props) {
  const title = chart.title ?? "Chart";

  if (chart.kind === "bignum") {
    const v = chart.value != null ? num(chart.value) : rows[0] && chart.y ? num(rows[0][chart.y]) : NaN;
    if (!Number.isFinite(v) && !rows.length) return null;
    const shown = Number.isFinite(v) ? v : 0;
    return (
      <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-4 text-center">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
          {chart.label || title}
        </p>
        <p className="mt-2 text-3xl font-semibold tabular-nums text-[var(--color-ink)]">
          {money(shown)}
        </p>
      </div>
    );
  }

  // The rows table (AnswerRowsTable) is already the view for ``table``.
  if (chart.kind === "table") return null;

  if (!rows.length || !chart.x || !chart.y) return null;

  if (chart.kind === "pie") {
    const slices = pieSlices(rows, chart.x, chart.y);
    if (slices.length < 2) return null;
    const size = 160;
    const c = size / 2;
    const r = c - 4;
    return (
      <Frame title={title}>
        <div className="flex flex-wrap items-center justify-center gap-4">
          <svg viewBox={`0 0 ${size} ${size}`} className="block h-40 w-40" role="img" aria-label={title}>
            {slices.map((sl, i) => (
              <path
                key={`${sl.label}-${i}`}
                d={arcPath(c, c, r, sl.startAngle, sl.endAngle)}
                fill={SERIES[i % SERIES.length]}
                stroke="var(--color-panel)"
                strokeWidth="2"
              >
                <title>{`${sl.label}: ${money(sl.value)} (${pct(sl.share)})`}</title>
              </path>
            ))}
          </svg>
          <ul className="space-y-1 text-sm">
            {slices.map((sl, i) => (
              <li key={`${sl.label}-${i}`} className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="inline-block h-3 w-3 shrink-0 rounded-sm"
                  style={{ background: SERIES[i % SERIES.length] }}
                />
                <span className="text-[var(--color-ink)]">{sl.label}</span>
                <span className="tabular-nums text-[var(--color-ink-muted)]">
                  {money(sl.value)} · {pct(sl.share)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </Frame>
    );
  }

  if (chart.kind === "scatter") {
    const pts = scatterPoints(rows, chart.x, chart.y);
    if (!pts.length) return null;
    const w = 280;
    const h = 160;
    const pad = { l: 44, r: 8, t: 8, b: 28 };
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const [xMin, xMax] = [Math.min(...xs), Math.max(...xs)];
    const [yMin, yMax] = [Math.min(...ys), Math.max(...ys)];
    const sx = (v: number) =>
      pad.l + (xMax === xMin ? 0.5 : (v - xMin) / (xMax - xMin)) * (w - pad.l - pad.r);
    const sy = (v: number) =>
      h - pad.b - (yMax === yMin ? 0.5 : (v - yMin) / (yMax - yMin)) * (h - pad.t - pad.b);
    const axis = { fontSize: 10, fill: "var(--color-ink-muted)" };
    return (
      <Frame title={title}>
        <svg viewBox={`0 0 ${w} ${h}`} className="mx-auto block w-full max-w-md" role="img" aria-label={title}>
          <line x1={pad.l} y1={h - pad.b} x2={w - pad.r} y2={h - pad.b} stroke="var(--color-line)" />
          <line x1={pad.l} y1={pad.t} x2={pad.l} y2={h - pad.b} stroke="var(--color-line)" />
          {pts.map((p) => (
            <circle
              key={p.row}
              cx={sx(p.x)}
              cy={sy(p.y)}
              r="4"
              fill="var(--color-accent)"
              stroke="var(--color-panel)"
              strokeWidth="2"
            >
              <title>{`${chart.x}: ${money(p.x)}, ${chart.y}: ${money(p.y)}`}</title>
            </circle>
          ))}
          <text x={pad.l} y={h - pad.b + 14} textAnchor="start" {...axis}>{money(xMin)}</text>
          <text x={w - pad.r} y={h - pad.b + 14} textAnchor="end" {...axis}>{money(xMax)}</text>
          <text x={w / 2} y={h - 2} textAnchor="middle" {...axis}>{chart.x}</text>
          <text x={pad.l - 4} y={h - pad.b} textAnchor="end" {...axis}>{money(yMin)}</text>
          <text x={pad.l - 4} y={pad.t + 8} textAnchor="end" {...axis}>{money(yMax)}</text>
        </svg>
        <p className="mt-1 text-center text-[11px] text-[var(--color-ink-muted)]">
          {chart.y} vs {chart.x}
        </p>
      </Frame>
    );
  }

  if (chart.kind === "line") {
    // The spec sorts x ascending; draw the same order without mutating rows.
    const key = chart.x;
    rows = [...rows].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (typeof av === "number" && typeof bv === "number") return av - bv;
      return label(av).localeCompare(label(bv));
    });
  }

  const vals = rows.map((r) => num(r[chart.y!]));
  const max = Math.max(...vals, 1);

  if (chart.kind === "hbar") {
    return (
      <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
          {title}
        </p>
        <ul className="space-y-2">
          {rows.map((r, i) => {
            const y = num(r[chart.y!]);
            const pct = Math.max(2, (y / max) * 100);
            return (
              <li key={`${label(r[chart.x!])}-${i}`} className="grid grid-cols-[7rem_1fr_4.5rem] items-center gap-2 text-sm">
                <span className="truncate text-[var(--color-ink)]">{label(r[chart.x!])}</span>
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

  // bar or line — same points; line connects tops
  const h = 120;
  const gap = 8;
  const barW = Math.min(48, Math.floor(280 / Math.max(rows.length, 1)));
  const width = rows.length * (barW + gap) + gap;
  const isLine = chart.kind === "line";
  const points = rows
    .map((r, i) => {
      const y = num(r[chart.y!]);
      const bh = Math.max(2, (y / max) * h);
      const x = gap + i * (barW + gap) + barW / 2;
      return `${x},${h - bh}`;
    })
    .join(" ");

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
        {isLine && rows.length > 1 && (
          <polyline
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="2"
            points={points}
          />
        )}
        {rows.map((r, i) => {
          const y = num(r[chart.y!]);
          const bh = Math.max(2, (y / max) * h);
          const x = gap + i * (barW + gap);
          return (
            <g key={`${label(r[chart.x!])}-${i}`}>
              {!isLine && (
                <rect
                  x={x}
                  y={h - bh}
                  width={barW}
                  height={bh}
                  fill="var(--color-accent)"
                />
              )}
              {isLine && (
                <circle
                  cx={x + barW / 2}
                  cy={h - bh}
                  r="3"
                  fill="var(--color-accent)"
                />
              )}
              <text
                x={x + barW / 2}
                y={h + 14}
                textAnchor="middle"
                fontSize="10"
                fill="var(--color-ink-muted)"
              >
                {label(r[chart.x!]).slice(0, 10)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
