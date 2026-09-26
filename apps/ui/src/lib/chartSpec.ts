/** DMS-VIZ-01 — pure geometry inputs for SimpleChart's pie and scatter.
 *
 * Every figure is read from the answer ``rows`` — never from the chart spec,
 * which names columns only. Non-finite cells (NaN, "n/a", null, Infinity) are
 * dropped rather than drawn as zero: a zero slice or a point at the origin
 * would be a figure the query never returned.
 */

export type PieSlice = {
  label: string;
  value: number;
  /** Fraction of the positive total, 0..1. */
  share: number;
  startAngle: number;
  endAngle: number;
};

export type ScatterPoint = { x: number; y: number; row: number };

function finite(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Slices in row order; angles in radians from 12 o'clock, clockwise. */
export function pieSlices(
  rows: Record<string, unknown>[],
  x: string,
  y: string,
): PieSlice[] {
  const kept: { label: string; value: number }[] = [];
  for (const r of rows) {
    const v = finite(r[y]);
    if (v == null || v <= 0) continue;
    kept.push({ label: r[x] == null ? "" : String(r[x]), value: v });
  }
  const total = kept.reduce((s, k) => s + k.value, 0);
  if (!(total > 0)) return [];
  let angle = 0;
  return kept.map((k) => {
    const share = k.value / total;
    const start = angle;
    angle += share * 2 * Math.PI;
    return { ...k, share, startAngle: start, endAngle: angle };
  });
}

/** Points whose x and y are both finite; ``row`` is the source row index. */
export function scatterPoints(
  rows: Record<string, unknown>[],
  x: string,
  y: string,
): ScatterPoint[] {
  const out: ScatterPoint[] = [];
  rows.forEach((r, i) => {
    const px = finite(r[x]);
    const py = finite(r[y]);
    if (px == null || py == null) return;
    out.push({ x: px, y: py, row: i });
  });
  return out;
}
