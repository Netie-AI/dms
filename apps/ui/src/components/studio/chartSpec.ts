/**
 * CHART-SPEC-01. Same shape rules as packages/executor/dms_executor/chart_spec.py.
 * Column types come from cell shapes (number, ISO date, text). Not from the question.
 * A spec that names a missing column is dropped.
 */

const ISO_DATE =
  /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

export const CHART_KINDS = ["bar", "line", "pie", "stacked", "multi_line", "scatter"] as const;
export type ChartKind = (typeof CHART_KINDS)[number];

export type ColumnType = "number" | "date" | "text" | "other" | "empty";

export type ModelChartSpec = {
  kind: ChartKind;
  x: string;
  y: string[];
  series: string | null;
  title?: string;
};

export type ChartAdjust = {
  kinds: ChartKind[];
  x: string[];
  y: string[];
  series: string[];
};

export type ChartChoice = {
  kind: string;
  x?: string;
  y?: string[];
  series?: string | null;
  repairX?: boolean;
  repairY?: boolean;
};

type Column = { name: string; type: ColumnType };

export function isKind(value: string): value is ChartKind {
  return (CHART_KINDS as readonly string[]).includes(value);
}

export function columnTypes(rows: Record<string, unknown>[]): Column[] {
  const names: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (key && !names.includes(key)) names.push(key);
    }
  }
  return names.map((name) => ({ name, type: columnType(rows, name) }));
}

export function validateModelSpec(
  spec: unknown,
  rows: Record<string, unknown>[],
): ModelChartSpec | null {
  const parsed = parseSpec(spec);
  if (!parsed) return null;
  const types = new Map(columnTypes(rows).map((col) => [col.name, col.type]));
  if (!shapeOk(parsed.kind, parsed.x, parsed.y, parsed.series, types)) return null;
  return parsed;
}

export function readAdjust(raw: unknown, rows: Record<string, unknown>[]): ChartAdjust | null {
  if (!raw || typeof raw !== "object") return null;
  const rec = raw as Record<string, unknown>;
  const names = new Set(columnTypes(rows).map((col) => col.name));
  const kinds = strings(rec.kinds).filter(isKind);
  const x = strings(rec.x).filter((name) => names.has(name));
  const y = strings(rec.y).filter((name) => names.has(name));
  const series = strings(rec.series).filter((name) => names.has(name));
  if (!kinds.length) return null;
  return { kinds, x, y, series };
}

export function adjustCatalog(rows: Record<string, unknown>[]): ChartAdjust {
  const cols = columnTypes(rows);
  const types = new Map(cols.map((col) => [col.name, col.type]));
  const kinds: ChartKind[] = [];
  const x: string[] = [];
  const y: string[] = [];
  const series: string[] = [];
  for (const kind of CHART_KINDS) {
    let found = false;
    for (const candidate of candidates(kind, types)) {
      if (!shapeOk(candidate.kind, candidate.x, candidate.y, candidate.series, types)) continue;
      found = true;
      if (!x.includes(candidate.x)) x.push(candidate.x);
      for (const name of candidate.y) if (!y.includes(name)) y.push(name);
      if (candidate.series && !series.includes(candidate.series)) series.push(candidate.series);
    }
    if (found) kinds.push(kind);
  }
  return { kinds, x, y, series };
}

/** A click becomes a spec only when `validateModelSpec` accepts it. */
export function chooseChart(
  rows: Record<string, unknown>[],
  adjust: ChartAdjust,
  choice: ChartChoice,
): ModelChartSpec | null {
  if (!isKind(choice.kind) || !adjust.kinds.includes(choice.kind)) return null;
  const requested = (choice.y ?? []).filter((name) => adjust.y.includes(name));
  const xs: string[] = [];
  if (choice.x && adjust.x.includes(choice.x)) xs.push(choice.x);
  if (choice.repairX !== false) {
    for (const name of adjust.x) if (!xs.includes(name)) xs.push(name);
  }
  const yLists = yOptions(choice.kind, requested, adjust, choice.repairY === false);
  const seriesList = seriesOptions(
    choice.kind,
    choice.series && adjust.series.includes(choice.series) ? choice.series : null,
    adjust,
  );
  for (const x of xs) {
    for (const y of yLists) {
      for (const series of seriesList) {
        const ok = validateModelSpec({ kind: choice.kind, x, y, series }, rows);
        if (ok) return ok;
      }
    }
  }
  return null;
}

export function kindLabel(kind: string): string {
  return kind === "multi_line" ? "multi-line" : kind;
}

function strings(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function columnType(rows: Record<string, unknown>[], name: string): ColumnType {
  let seen: ColumnType | null = null;
  for (const row of rows) {
    if (!Object.prototype.hasOwnProperty.call(row, name)) continue;
    const next = valueType(row[name]);
    if (next == null) continue;
    if (seen && seen !== next) return "other";
    seen = next;
  }
  return seen ?? "empty";
}

function valueType(value: unknown): ColumnType | null {
  if (value == null || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) return "number";
  if (typeof value === "string") return ISO_DATE.test(value.trim()) ? "date" : "text";
  return "other";
}

function parseSpec(spec: unknown): ModelChartSpec | null {
  if (!spec || typeof spec !== "object") return null;
  const rec = spec as Record<string, unknown>;
  if (typeof rec.kind !== "string" || !isKind(rec.kind)) return null;
  if (typeof rec.x !== "string" || !rec.x) return null;
  const rawY = Array.isArray(rec.y) ? rec.y : typeof rec.y === "string" ? [rec.y] : null;
  if (!rawY || !rawY.length) return null;
  const y: string[] = [];
  for (const item of rawY) {
    if (typeof item !== "string" || !item) return null;
    y.push(item);
  }
  let series: string | null = null;
  if (typeof rec.series === "string" && rec.series) series = rec.series;
  else if (rec.series != null && rec.series !== "") return null;
  return { kind: rec.kind, x: rec.x, y, series };
}

function shapeOk(
  kind: ChartKind,
  x: string,
  y: string[],
  series: string | null,
  types: Map<string, ColumnType>,
): boolean {
  if (!types.has(x) || y.some((name) => !types.has(name))) return false;
  if (series != null && !types.has(series)) return false;
  if (y.some((name) => types.get(name) !== "number")) return false;
  if (new Set(y).size !== y.length || y.includes(x)) return false;
  if (series != null && (series === x || y.includes(series))) return false;
  const xt = types.get(x);
  const cat = xt === "text" || xt === "date";
  if (kind === "bar" || kind === "pie") return cat && y.length === 1 && series == null;
  if (kind === "line") return (cat || xt === "number") && y.length === 1 && series == null;
  if (kind === "scatter") return xt === "number" && y.length === 1 && series == null;
  if (kind === "stacked" && !cat) return false;
  if (kind === "multi_line" && !(cat || xt === "number")) return false;
  if (kind !== "stacked" && kind !== "multi_line") return false;
  if (series == null) return y.length >= 2;
  const st = types.get(series);
  return y.length === 1 && (st === "text" || st === "date");
}

function candidates(kind: ChartKind, types: Map<string, ColumnType>): ModelChartSpec[] {
  const numbers = [...types].filter(([, typ]) => typ === "number").map(([name]) => name);
  const cats = [...types].filter(([, typ]) => typ === "text" || typ === "date").map(([name]) => name);
  const axes = [...types]
    .filter(([, typ]) => typ === "text" || typ === "date" || typ === "number")
    .map(([name]) => name);
  const out: ModelChartSpec[] = [];
  if (kind === "bar" || kind === "pie") {
    for (const x of cats) for (const y of numbers) out.push({ kind, x, y: [y], series: null });
  } else if (kind === "line") {
    for (const x of axes) {
      for (const y of numbers) if (x !== y) out.push({ kind, x, y: [y], series: null });
    }
  } else if (kind === "scatter") {
    for (const x of numbers) {
      for (const y of numbers) if (x !== y) out.push({ kind, x, y: [y], series: null });
    }
  } else {
    const xs = kind === "stacked" ? cats : axes;
    for (const x of xs) {
      const y = numbers.filter((name) => name !== x);
      if (y.length >= 2) out.push({ kind, x, y, series: null });
      for (const measure of y) {
        for (const series of cats) out.push({ kind, x, y: [measure], series });
      }
    }
  }
  return out;
}

function yOptions(kind: ChartKind, requested: string[], adjust: ChartAdjust, lock: boolean): string[][] {
  if (lock) return [requested];
  const out: string[][] = [];
  const push = (names: string[]) => {
    const key = names.join("\0");
    if (!out.some((item) => item.join("\0") === key)) out.push(names);
  };
  if (requested.length) push(requested);
  if ((kind === "stacked" || kind === "multi_line") && adjust.y.length >= 2) push(adjust.y.slice());
  for (const name of adjust.y) push([name]);
  return out.length ? out : [[]];
}

function seriesOptions(kind: ChartKind, requested: string | null, adjust: ChartAdjust): (string | null)[] {
  if (kind !== "stacked" && kind !== "multi_line") return [null];
  const out: (string | null)[] = [];
  const push = (name: string | null) => {
    if (!out.includes(name)) out.push(name);
  };
  if (requested) push(requested);
  push(null);
  for (const name of adjust.series) push(name);
  return out;
}
