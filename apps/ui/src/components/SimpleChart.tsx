import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatCellValue } from "@/lib/formatCellValue";
import type { ChartSpec } from "@/lib/types";
import { useIsLg } from "@/lib/viewport";

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

const PANEL =
  "mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3 max-lg:min-h-[200px]";
const TITLE =
  "mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]";
const FOCUS =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]";

/** One chart point as the answer's table shows it: same label, same value format. */
type Point = { name: string; value: number; shown: string; aria: string };

/**
 * The exact figure of the selected point, under the chart. A readout row, not a hover
 * tooltip, so it works by touch. Below lg the row is always reserved (a hint while
 * nothing is selected) so selecting never shifts the layout; on desktop it exists only
 * while something is selected.
 */
function Readout({ point, hint }: { point: Point | null; hint: ReactNode }) {
  return (
    <div aria-live="polite" className="max-lg:min-h-11">
      {point ? (
        <p
          data-testid="chart-readout"
          className="mt-2 flex items-baseline justify-between gap-3 border-t border-[var(--color-line)] pt-2 text-sm text-[var(--color-ink)] max-lg:min-h-11"
        >
          <span className="min-w-0 break-words">{point.name}</span>
          <span className="shrink-0 font-semibold tabular-nums">{point.shown}</span>
        </p>
      ) : (
        <p className="mt-2 text-[11px] text-[var(--color-ink-muted)] lg:hidden">{hint}</p>
      )}
    </div>
  );
}

/** Pure CSS/SVG charts — no chart library. Cortex chart_spec maps here. */
export function SimpleChart({ chart, rows }: Props) {
  const isLg = useIsLg();
  const [selected, setSelected] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const scroller = useRef<HTMLDivElement | null>(null);
  const title = chart.title ?? "Chart";
  const x = chart.x;
  const y = chart.y;
  const vertical = chart.kind !== "bignum" && chart.kind !== "hbar";
  const phoneColumns = vertical && !isLg;
  const count = rows.length;

  // A cue that more columns sit off to the right, live as the person scrolls or rotates.
  useEffect(() => {
    const el = scroller.current;
    if (!phoneColumns || !el) return;
    const update = () => setMore(el.scrollWidth - el.clientWidth - el.scrollLeft > 2);
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro?.disconnect();
    };
  }, [phoneColumns, count]);

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

  if (!rows.length || !x || !y) return null;

  const vals = rows.map((r) => num(r[y]));
  const max = Math.max(...vals, 1);
  const points: Point[] = rows.map((r, i) => {
    const name = label(r[x]);
    const shown = formatCellValue(r[y]);
    return { name, value: vals[i], shown, aria: `${name}: ${shown}` };
  });
  const picked = selected != null && selected < count ? selected : null;
  const toggle = (i: number) => setSelected((s) => (s === i ? null : i));
  const current = picked != null ? points[picked] : null;
  const barHint = "Tap a bar to read its exact value.";

  if (chart.kind === "hbar") {
    return (
      <div data-testid="answer-chart" className={PANEL}>
        <p className={TITLE}>{title}</p>
        <ul className="space-y-2">
          {points.map((p, i) => {
            const pct = Math.max(2, (p.value / max) * 100);
            const on = picked === i;
            return (
              <li key={`${p.name}-${i}`}>
                <button
                  type="button"
                  aria-label={p.aria}
                  aria-pressed={on}
                  onClick={() => toggle(i)}
                  className={`grid w-full grid-cols-[7rem_1fr_4.5rem] items-center gap-2 text-left text-sm max-lg:min-h-11 ${FOCUS} ${
                    on ? "bg-[var(--color-accent-soft)]" : ""
                  }`}
                >
                  <span data-chart-label className="truncate text-[var(--color-ink)]">
                    {p.name}
                  </span>
                  <span className="block h-3 overflow-hidden bg-[var(--color-paper-2)]">
                    <span
                      className="block h-full bg-[var(--color-accent)] transition-[width] duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </span>
                  <span className="text-right tabular-nums text-[var(--color-ink-muted)]">
                    {money(p.value)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        <Readout point={current} hint={barHint} />
      </div>
    );
  }

  const isLine = chart.kind === "line";

  if (phoneColumns) {
    // Phone: real columns. Each is a full-height button at least 44px wide, so a thin
    // or zero-value bar is still easy to hit. Past what fits, the box scrolls sideways.
    const PLOT = 180;
    const pctOf = (v: number) => Math.min(100, Math.max(0, (v / max) * 100));
    const line = points.map((p, i) => `${i + 0.5},${100 - pctOf(p.value)}`).join(" ");
    return (
      <div data-testid="answer-chart" className={PANEL}>
        <p className={TITLE}>{title}</p>
        <div className="relative">
          <div
            ref={scroller}
            data-testid="chart-scroll"
            className="overflow-x-auto overscroll-x-contain"
          >
            <div className="relative flex min-w-full">
              {isLine && count > 1 && (
                <svg
                  aria-hidden="true"
                  viewBox={`0 0 ${count} 100`}
                  preserveAspectRatio="none"
                  className="pointer-events-none absolute left-0 top-0 w-full"
                  style={{ height: PLOT }}
                >
                  <polyline
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth="2"
                    vectorEffect="non-scaling-stroke"
                    points={line}
                  />
                </svg>
              )}
              {points.map((p, i) => {
                const on = picked === i;
                return (
                  <button
                    key={`${p.name}-${i}`}
                    type="button"
                    aria-label={p.aria}
                    aria-pressed={on}
                    onClick={() => toggle(i)}
                    className={`flex min-w-12 flex-1 flex-col ${FOCUS} ${
                      on ? "bg-[var(--color-accent-soft)]" : ""
                    }`}
                  >
                    <span className="relative block w-full" style={{ height: PLOT }}>
                      {isLine ? (
                        <span
                          className="absolute left-1/2 block h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[var(--color-accent)]"
                          style={{ bottom: `calc(${pctOf(p.value)}% - 5px)` }}
                        />
                      ) : (
                        <span
                          className="absolute bottom-0 left-1/2 block w-8 -translate-x-1/2 bg-[var(--color-accent)]"
                          style={{ height: `max(2px, ${pctOf(p.value)}%)` }}
                        />
                      )}
                    </span>
                    <span
                      data-chart-label
                      className="block h-6 w-full truncate px-0.5 text-center text-[11px] leading-6 text-[var(--color-ink-muted)]"
                    >
                      {p.name}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          {more && (
            <span
              data-testid="chart-scroll-cue"
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 right-0 flex w-8 items-center justify-end bg-gradient-to-l from-[var(--color-panel)] to-transparent pr-1 text-base font-semibold text-[var(--color-ink-muted)]"
            >
              ›
            </span>
          )}
        </div>
        <Readout
          point={current}
          hint={more ? "Swipe sideways for more. Tap a bar to read its exact value." : barHint}
        />
      </div>
    );
  }

  // Desktop: the SVG as it always was. Each column is now a focusable button (Enter or
  // Space selects), with a transparent hit area so a line's small dot is reachable too.
  const h = 120;
  const gap = 8;
  const barW = Math.min(48, Math.floor(280 / Math.max(count, 1)));
  const width = count * (barW + gap) + gap;
  const polyline = vals
    .map((v, i) => {
      const bh = Math.max(2, (v / max) * h);
      const px = gap + i * (barW + gap) + barW / 2;
      return `${px},${h - bh}`;
    })
    .join(" ");

  return (
    <div data-testid="answer-chart" className={PANEL}>
      <p className={TITLE}>{title}</p>
      <svg
        viewBox={`0 0 ${width} ${h + 28}`}
        className="mx-auto block w-full max-w-md"
        role="group"
        aria-label={title}
      >
        {isLine && count > 1 && (
          <polyline fill="none" stroke="var(--color-accent)" strokeWidth="2" points={polyline} />
        )}
        {points.map((p, i) => {
          const bh = Math.max(2, (p.value / max) * h);
          const px = gap + i * (barW + gap);
          const on = picked === i;
          return (
            <g
              key={`${p.name}-${i}`}
              role="button"
              tabIndex={0}
              aria-label={p.aria}
              aria-pressed={on}
              className={`cursor-pointer ${FOCUS}`}
              onClick={() => toggle(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(i);
                }
              }}
            >
              <rect x={px} y={0} width={barW} height={h + 28} fill="transparent" />
              {!isLine && (
                <rect
                  x={px}
                  y={h - bh}
                  width={barW}
                  height={bh}
                  fill="var(--color-accent)"
                  stroke={on ? "var(--color-ink)" : "none"}
                  strokeWidth={on ? 1.5 : 0}
                />
              )}
              {isLine && (
                <circle
                  cx={px + barW / 2}
                  cy={h - bh}
                  r={on ? 4.5 : 3}
                  fill="var(--color-accent)"
                />
              )}
              <text
                x={px + barW / 2}
                y={h + 14}
                textAnchor="middle"
                fontSize="10"
                fill="var(--color-ink-muted)"
              >
                {p.name.slice(0, 10)}
              </text>
            </g>
          );
        })}
      </svg>
      <Readout point={current} hint={barHint} />
    </div>
  );
}
