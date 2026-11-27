import type { ReactNode } from "react";
import type { ModelChartSpec } from "@/components/studio/chartSpec";
import type { ChartSpec } from "@/lib/types";

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

/** Pure CSS/SVG charts. No chart library. New kinds read result cells only. */
export function SimpleChart({ chart, rows }: Props) {
  if (chart.kind === "pie") return <PieChart chart={chart} rows={rows} />;
  if (chart.kind === "stacked") return <StackedChart chart={chart} rows={rows} />;
  if (chart.kind === "multi_line") return <MultiLineChart chart={chart} rows={rows} />;
  if (chart.kind === "scatter") return <ScatterChart chart={chart} rows={rows} />;

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

  const yName = typeof chart.y === "string" ? chart.y : Array.isArray(chart.y) ? chart.y[0] : undefined;
  if (!rows.length || !chart.x || !yName) return null;

  const vals = rows.map((r) => num(r[yName]));
  const max = Math.max(...vals, 1);

  if (chart.kind === "hbar") {
    return (
      <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
          {title}
        </p>
        <ul className="space-y-2">
          {rows.map((r, i) => {
            const y = num(r[yName]);
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

  // bar or line: same points; line connects tops
  const h = 120;
  const gap = 8;
  const barW = Math.min(48, Math.floor(280 / Math.max(rows.length, 1)));
  const width = rows.length * (barW + gap) + gap;
  const isLine = chart.kind === "line";
  const points = rows
    .map((r, i) => {
      const y = num(r[yName]);
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
          const y = num(r[yName]);
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
