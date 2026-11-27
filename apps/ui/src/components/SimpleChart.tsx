import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatCellValue } from "@/lib/formatCellValue";
import type { ModelChartSpec } from "@/components/studio/chartSpec";
import type { ChartSpec } from "@/lib/types";
import { useIsLg } from "@/lib/viewport";

type Props = {
  chart: ChartSpec | ModelChartSpec;
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

/** A plotted number is a finite result cell. Missing and non-numeric cells are skipped. */
function finiteCell(row: Record<string, unknown>, key: string): number | null {
  const v = row[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

const PANEL =
  "mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3 max-lg:min-h-[200px]";
// 11px on desktop as it always was; below lg nothing in a chart is under 12px.
const TITLE_SIZE = "max-lg:text-xs lg:text-[11px]";
const TITLE = `mb-3 ${TITLE_SIZE} font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]`;
// A negative value is hatched in the danger colour (a pattern, not only a colour), so it never
// reads as a zero bar or as a positive one. Below lg only.
const NEG_MARK = {
  background: "repeating-linear-gradient(135deg, var(--color-danger) 0 3px, transparent 3px 6px)",
  boxShadow: "inset 0 0 0 1px var(--color-danger)",
};
const FOCUS =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--color-accent)]";

/** One chart point as the answer's table shows it: same label, same value format. */
type Point = {
  /** Label for the readout and aria: a placeholder when the row has none. */
  name: string;
  /** The label as the row has it ("" when null), which desktop draws as it always did. */
  raw: string;
  value: number;
  shown: string;
  aria: string;
  neg: boolean;
};

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
          <span className="min-w-0 break-words line-clamp-3" title={point.name}>
            {point.name}
          </span>
          <span className="shrink-0 font-semibold tabular-nums">
            {point.neg && (
              <span className="mr-1 font-normal text-[var(--color-danger)]">negative</span>
            )}
            {point.shown}
          </span>
        </p>
      ) : (
        <p className="mt-2 text-xs text-[var(--color-ink-muted)] lg:hidden">{hint}</p>
      )}
    </div>
  );
}

/**
 * Pure CSS/SVG charts. No chart library. New kinds read result cells only. The dispatch is a
 * component of its own so the hooks below never run for a pie, stacked, multi-line or scatter chart
 * (a hook count that changed with the kind would be a React error).
 */
export function SimpleChart({ chart, rows }: Props) {
  if (chart.kind === "pie") return <PieChart chart={chart} rows={rows} />;
  if (chart.kind === "stacked") return <StackedChart chart={chart} rows={rows} />;
  if (chart.kind === "multi_line") return <MultiLineChart chart={chart} rows={rows} />;
  if (chart.kind === "scatter") return <ScatterChart chart={chart} rows={rows} />;
  return <BarLineChart chart={chart} rows={rows} />;
}

/** Bar, hbar, line and bignum: the phone columns, the readout and the desktop SVG. */
function BarLineChart({ chart, rows }: Props) {
  const isLg = useIsLg();
  const [selected, setSelected] = useState<number | null>(null);
  const [more, setMore] = useState(false);
  const scroller = useRef<HTMLDivElement | null>(null);
  const title = chart.title ?? "Chart";
  const x = chart.x;
  const y = typeof chart.y === "string" ? chart.y : Array.isArray(chart.y) ? chart.y[0] : undefined;
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
        <p className={`${TITLE_SIZE} font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]`}>
          {chart.label || title}
        </p>
        <p className="mt-2 text-3xl font-semibold tabular-nums text-[var(--color-ink)]">
          {money(shown)}
        </p>
      </div>
    );
  }

  if (!rows.length || !x || !y) return null;
  // One bar says nothing: the sentence and the table above already state the value.
  if (!isLg && rows.length === 1) return null;

  const vals = rows.map((r) => num(r[y]));
  const max = Math.max(...vals, 1);
  // Below lg bars scale by magnitude so a negative value has a real size, not a 2px sliver.
  const absMax = Math.max(...vals.map(Math.abs), 1);
  const points: Point[] = rows.map((r, i) => {
    const raw = label(r[x]);
    const name = raw === "" ? "(no label)" : raw;
    const shown = formatCellValue(r[y]) || "no value";
    const neg = vals[i] < 0;
    return {
      name,
      raw,
      value: vals[i],
      shown,
      aria: `${name}: ${shown}${neg ? ", negative" : ""}`,
      neg,
    };
  });
  const picked = selected != null && selected < count ? selected : null;
  const toggle = (i: number) => setSelected((s) => (s === i ? null : i));
  const current = picked != null ? points[picked] : null;
  const barHint = "Tap a bar to read its exact value.";

  if (chart.kind === "hbar") {
    return (
      <div data-testid="answer-chart" className={PANEL}>
        <p className={TITLE}>{title}</p>
        {/* Text size sits on the li: index.css resets font on buttons, so a size utility on
            the button itself would not apply and the row would render at the inherited 16px. */}
        <ul className="space-y-2">
          {points.map((p, i) => {
            const useNeg = !isLg && p.neg;
            const pct = isLg
              ? Math.max(2, (p.value / max) * 100)
              : Math.max(2, (Math.abs(p.value) / absMax) * 100);
            const on = picked === i;
            return (
              <li key={`${p.name}-${i}`} className="text-sm">
                <button
                  type="button"
                  aria-label={p.aria}
                  aria-pressed={on}
                  onClick={() => toggle(i)}
                  className={`grid w-full grid-cols-[7rem_1fr_4.5rem] items-center gap-2 text-left max-lg:min-h-11 ${FOCUS} ${
                    on ? "bg-[var(--color-accent-soft)]" : ""
                  }`}
                >
                  <span data-chart-label className="truncate text-[var(--color-ink)]">
                    {isLg ? p.raw : p.name}
                  </span>
                  <span className="block h-3 overflow-hidden bg-[var(--color-paper-2)]">
                    <span
                      data-testid="chart-mark"
                      className="block h-full bg-[var(--color-accent)] transition-[width] duration-500"
                      style={useNeg ? { width: `${pct}%`, ...NEG_MARK } : { width: `${pct}%` }}
                    />
                  </span>
                  <span
                    className={`text-right tabular-nums text-[var(--color-ink-muted)] ${on ? "max-lg:text-[var(--color-ink)]" : ""}`}
                  >
                    {isLg || p.shown !== "no value" ? money(p.value) : p.shown}
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
    // Bars: height is the magnitude. A line: height is the value, floored at the axis.
    const pctOf = (v: number) => Math.min(100, (Math.abs(v) / absMax) * 100);
    const lineOf = (v: number) => Math.min(100, Math.max(0, (v / max) * 100));
    const line = points.map((p, i) => `${i + 0.5},${100 - lineOf(p.value)}`).join(" ");
    return (
      <div data-testid="answer-chart" className={PANEL}>
        <p className={TITLE}>{title}</p>
        <div className="relative">
          <div
            ref={scroller}
            data-testid="chart-scroll"
            className={`overflow-x-auto overscroll-x-contain ${isLine ? "pt-2" : ""}`}
          >
            {/* Past what fits, the columns (3rem each at least) overflow the box. A line's svg is as wide
                as this row, so the row must be as wide as the columns or the polyline is squeezed into
                the first screenful and misses its own dots. */}
            <div
              className="relative flex min-w-full"
              style={isLine ? { minWidth: `max(100%, ${count * 3}rem)` } : undefined}
            >
              {isLine && count > 1 && (
                <svg
                  aria-hidden="true"
                  viewBox={`0 0 ${count} 100`}
                  preserveAspectRatio="none"
                  className="pointer-events-none absolute left-0 top-0 w-full overflow-visible"
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
                          style={{
                            bottom: `calc(${lineOf(p.value)}% - 5px)`,
                            ...(p.neg ? { background: "var(--color-danger)" } : {}),
                          }}
                        />
                      ) : (
                        <span
                          data-testid="chart-mark"
                          className="absolute bottom-0 left-1/2 block w-8 -translate-x-1/2 bg-[var(--color-accent)]"
                          style={{
                            height: `max(2px, ${pctOf(p.value)}%)`,
                            ...(p.neg ? NEG_MARK : {}),
                          }}
                        />
                      )}
                    </span>
                    <span
                      data-chart-label
                      className={`block h-6 w-full truncate px-0.5 text-center text-xs leading-6 text-[var(--color-ink-muted)] ${
                        on ? "max-lg:text-[var(--color-ink)]" : ""
                      }`}
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
                {p.raw.slice(0, 10)}
              </text>
            </g>
          );
        })}
      </svg>
      <Readout point={current} hint={barHint} />
    </div>
  );
}

// ponytail: five fills, then they repeat. Upgrade: more theme tokens for a wide series.
const FILLS = [
  "var(--color-accent)",
  "var(--color-warn)",
  "var(--color-badge-ok)",
  "var(--color-danger)",
  "var(--color-ink-muted)",
];

function Frame({ title, shape, children }: { title: string; shape: string; children: ReactNode }) {
  return (
    <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
      <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        {title}
      </p>
      <svg
        viewBox="0 0 280 160"
        className="mx-auto block w-full max-w-md"
        role="img"
        aria-label={title}
        data-chart-shape={shape}
      >
        {children}
      </svg>
    </div>
  );
}

function PieChart({ chart, rows }: { chart: ModelChartSpec; rows: Record<string, unknown>[] }) {
  const yKey = chart.y[0];
  if (!chart.x || !yKey) return null;
  const parts: { key: string; label: string; value: number }[] = [];
  rows.forEach((row, i) => {
    const value = finiteCell(row, yKey);
    // A non-positive cell is not drawn as a wedge. It is not flipped to a positive.
    if (value == null || value <= 0) return;
    parts.push({ key: String(i), label: label(row[chart.x]), value });
  });
  if (!parts.length) return null;
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const title = chart.title ?? "Chart";
  let angle = 0;
  const slices = parts.map((part, i) => {
    const sweep = (part.value / total) * Math.PI * 2;
    const start = angle;
    angle += sweep;
    return { ...part, start, end: angle, fill: FILLS[i % FILLS.length] };
  });
  return (
    <Frame title={title} shape="pie">
      {slices.map((slice) => (
        <path
          key={slice.key}
          d={piePath(78, 78, 58, slice.start, slice.end)}
          fill={slice.fill}
          data-cell={String(slice.value)}
        />
      ))}
      {slices.map((slice, i) => (
        <text key={`l-${slice.key}`} x={150} y={22 + i * 14} fontSize="10" fill="var(--color-ink)">
          {slice.label.slice(0, 12)}
        </text>
      ))}
    </Frame>
  );
}

function piePath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const sweep = a1 - a0;
  const p = (value: number) => String(Math.round(value * 100) / 100);
  if (sweep >= Math.PI * 2 - 1e-4) {
    return `M ${p(cx - r)} ${p(cy)} A ${r} ${r} 0 1 1 ${p(cx + r)} ${p(cy)} A ${r} ${r} 0 1 1 ${p(cx - r)} ${p(cy)} Z`;
  }
  const x1 = cx + r * Math.sin(a0);
  const y1 = cy - r * Math.cos(a0);
  const x2 = cx + r * Math.sin(a1);
  const y2 = cy - r * Math.cos(a1);
  const large = sweep > Math.PI ? 1 : 0;
  return `M ${p(cx)} ${p(cy)} L ${p(x1)} ${p(y1)} A ${r} ${r} 0 ${large} 1 ${p(x2)} ${p(y2)} Z`;
}

type Part = { name: string; value: number };
type Group = { label: string; parts: Part[] };

function StackedChart({ chart, rows }: { chart: ModelChartSpec; rows: Record<string, unknown>[] }) {
  const groups = positiveGroups(stackGroups(chart, rows));
  if (!groups.length) return null;
  const max = Math.max(...groups.map((group) => group.parts.reduce((sum, part) => sum + part.value, 0)), 1);
  const h = 120;
  const gap = 8;
  const barW = Math.min(48, Math.floor(240 / Math.max(groups.length, 1)));
  const names = seriesNames(groups);
  return (
    <Frame title={chart.title ?? "Chart"} shape="stacked">
      {groups.map((group, i) => {
        const x = gap + i * (barW + gap);
        let cursor = h;
        return (
          <g key={`${group.label}-${i}`}>
            {group.parts.map((part, p) => {
              const bh = Math.max(2, (part.value / max) * h);
              cursor -= bh;
              return (
                <rect
                  key={`${part.name}-${p}`}
                  x={x}
                  y={cursor}
                  width={barW}
                  height={bh}
                  fill={FILLS[names.indexOf(part.name) % FILLS.length]}
                  data-cell={String(part.value)}
                  data-series={part.name}
                />
              );
            })}
            <text x={x + barW / 2} y={h + 14} textAnchor="middle" fontSize="10" fill="var(--color-ink-muted)">
              {group.label.slice(0, 10)}
            </text>
          </g>
        );
      })}
    </Frame>
  );
}

function stackGroups(chart: ModelChartSpec, rows: Record<string, unknown>[]): Group[] {
  const yKey = chart.y[0];
  if (chart.series && yKey) {
    const order: string[] = [];
    const grouped = new Map<string, Part[]>();
    for (const row of rows) {
      const value = finiteCell(row, yKey);
      if (value == null) continue;
      const xv = label(row[chart.x]);
      if (!grouped.has(xv)) {
        grouped.set(xv, []);
        order.push(xv);
      }
      grouped.get(xv)!.push({ name: label(row[chart.series]), value });
    }
    return order.map((xv) => ({ label: xv, parts: grouped.get(xv) ?? [] }));
  }
  return rows.map((row) => ({
    label: label(row[chart.x]),
    parts: chart.y.flatMap((name) => {
      const value = finiteCell(row, name);
      return value == null ? [] : [{ name, value }];
    }),
  }));
}

function positiveGroups(groups: Group[]): Group[] {
  return groups
    .map((group) => ({ ...group, parts: group.parts.filter((part) => part.value > 0) }))
    .filter((group) => group.parts.length > 0);
}

function seriesNames(groups: Group[]): string[] {
  const names: string[] = [];
  for (const group of groups) {
    for (const part of group.parts) if (!names.includes(part.name)) names.push(part.name);
  }
  return names;
}

type LinePoint = { x: number; value: number };

function MultiLineChart({ chart, rows }: { chart: ModelChartSpec; rows: Record<string, unknown>[] }) {
  const lines = lineSeries(chart, rows).filter((line) => line.points.length > 0);
  if (!lines.length) return null;
  const values = lines.flatMap((line) => line.points.map((point) => point.value));
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  const h = 120;
  const width = 260;
  const slots = Math.max(...lines.flatMap((line) => line.points.map((point) => point.x)), 0) + 1;
  const sy = (value: number) => h - ((value - min) / span) * h;
  const sx = (slot: number) => (slots <= 1 ? width / 2 : 16 + (slot / (slots - 1)) * (width - 32));
  return (
    <Frame title={chart.title ?? "Chart"} shape="multi_line">
      {lines.map((line, i) => {
        const pts = line.points.map((point) => `${sx(point.x)},${sy(point.value)}`).join(" ");
        const fill = FILLS[i % FILLS.length];
        return (
          <g key={line.name}>
            {line.points.length > 1 ? (
              <polyline data-series={line.name} fill="none" stroke={fill} strokeWidth="2" points={pts} />
            ) : null}
            {line.points.map((point, p) => (
              <circle
                key={`${line.name}-${p}`}
                data-series={line.name}
                data-cell={String(point.value)}
                cx={sx(point.x)}
                cy={sy(point.value)}
                r="3"
                fill={fill}
              />
            ))}
          </g>
        );
      })}
    </Frame>
  );
}

function lineSeries(chart: ModelChartSpec, rows: Record<string, unknown>[]): { name: string; points: LinePoint[] }[] {
  const yKey = chart.y[0];
  if (chart.series && yKey) {
    const cats: string[] = [];
    for (const row of rows) {
      const xv = label(row[chart.x]);
      if (!cats.includes(xv)) cats.push(xv);
    }
    const order: string[] = [];
    const by = new Map<string, LinePoint[]>();
    for (const row of rows) {
      const value = finiteCell(row, yKey);
      if (value == null) continue;
      const name = label(row[chart.series]);
      if (!by.has(name)) {
        by.set(name, []);
        order.push(name);
      }
      by.get(name)!.push({ x: Math.max(cats.indexOf(label(row[chart.x])), 0), value });
    }
    return order.map((name) => ({ name, points: by.get(name) ?? [] }));
  }
  return chart.y.map((name) => ({
    name,
    points: rows.flatMap((row, i) => {
      const value = finiteCell(row, name);
      return value == null ? [] : [{ x: i, value }];
    }),
  }));
}

function ScatterChart({ chart, rows }: { chart: ModelChartSpec; rows: Record<string, unknown>[] }) {
  const yKey = chart.y[0];
  if (!chart.x || !yKey) return null;
  const points = rows.flatMap((row) => {
    const x = finiteCell(row, chart.x);
    const y = finiteCell(row, yKey);
    if (x == null || y == null) return [];
    return [{ x, y }];
  });
  if (!points.length) return null;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const sx = (value: number) => (maxX === minX ? 140 : 16 + ((value - minX) / (maxX - minX)) * 248);
  const sy = (value: number) => (maxY === minY ? 70 : 130 - ((value - minY) / (maxY - minY)) * 110);
  return (
    <Frame title={chart.title ?? "Chart"} shape="scatter">
      {points.map((point, i) => (
        <circle
          key={i}
          data-x={String(point.x)}
          data-y={String(point.y)}
          cx={sx(point.x)}
          cy={sy(point.y)}
          r="4"
          fill="var(--color-accent)"
        />
      ))}
    </Frame>
  );
}
