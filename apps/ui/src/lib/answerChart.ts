/**
 * UX-CHART-01. Chart type from the result shape. Cell values only.
 * No question text. No second query. No row cap.
 */

export type ChartKind = "bar" | "line" | "scatter" | "kpi" | "table";

export type ChartView = {
  kind: ChartKind;
  x: string;
  y: string;
};

export type FigureValue = {
  id: string;
  value: number;
  label: string;
  unit?: string;
};

export type SummaryPart =
  | { kind: "text"; text: string }
  | { kind: "figure"; id: string; text: string };

type ColumnKind = "numeric" | "date" | "category" | "other";

const ISO_DATE =
  /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

const UNIT_SUFFIX: [string, string][] = [
  ["_myr", "RM "],
  ["_usd", "USD "],
  ["_eur", "EUR "],
  ["_gbp", "GBP "],
  ["_sgd", "SGD "],
];

const FOUND_LINE = /^Found \d+ row\(s\)\.$/;

export function close(left: number, right: number): boolean {
  return Math.abs(left - right) < 0.011 || Math.abs(left - right) / Math.max(Math.abs(right), 1e-9) < 1e-4;
}

export function formatGrouped(n: number): string {
  const sign = n < 0 ? "-" : "";
  const [whole, frac] = Math.abs(n).toFixed(2).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${grouped}.${frac}`;
}

export function currencyPrefix(column: string, values: { label: string; unit?: string }[]): string {
  for (const item of values) {
    if (item.label !== column || !item.unit) continue;
    const unit = item.unit.trim().toUpperCase();
    if (unit === "MYR" || unit === "RM") return "RM ";
    if (unit === "USD" || unit === "EUR" || unit === "GBP" || unit === "SGD") return `${unit} `;
  }
  const low = column.toLowerCase();
  for (const [suffix, prefix] of UNIT_SUFFIX) {
    if (low.endsWith(suffix)) return prefix;
  }
  return "";
}

export function formatFigure(
  n: number,
  column: string,
  values: { label: string; unit?: string }[],
): string {
  return currencyPrefix(column, values) + formatGrouped(n);
}

export function connectorButtonLabel(live: boolean, product: string): string {
  return live ? product : "Export file";
}

export function chartShape(rows: Record<string, unknown>[]): ChartView | null {
  if (!rows.length) return null;
  const cols = columnKinds(rows);
  const nums = names(cols, "numeric");
  const dates = names(cols, "date");
  const cats = names(cols, "category");
  if (!nums.length) return null;
  if (rows.length === 1 && nums.length === 1) {
    return { kind: "kpi", x: cats[0] ?? dates[0] ?? "", y: nums[0] };
  }
  if (dates.length && nums.length) return { kind: "line", x: dates[0], y: nums[0] };
  if (nums.length >= 2) return { kind: "scatter", x: nums[0], y: nums[1] };
  if (cats.length) return { kind: "bar", x: cats[0], y: nums[0] };
  return null;
}

export function activeChart(rows: Record<string, unknown>[], pick: ChartKind | null): ChartView {
  const shape = chartShape(rows);
  const kind = pick ?? shape?.kind ?? "table";
  if (kind === "table") return { kind: "table", x: "", y: "" };
  const cols = columnKinds(rows);
  const nums = names(cols, "numeric");
  const dates = names(cols, "date");
  const cats = names(cols, "category");
  if (!nums.length) return { kind: "table", x: "", y: "" };
  if (kind === "kpi") return { kind: "kpi", x: cats[0] ?? dates[0] ?? "", y: nums[0] };
  if (kind === "scatter") {
    if (nums.length < 2) return shape ?? { kind: "table", x: "", y: "" };
    return { kind: "scatter", x: nums[0], y: nums[1] };
  }
  if (kind === "line") return { kind: "line", x: dates[0] ?? cats[0] ?? "", y: nums[0] };
  return { kind: "bar", x: cats[0] ?? dates[0] ?? "", y: nums[0] };
}

export function switcherKinds(shape: ChartView | null): ChartKind[] {
  const kinds: ChartKind[] = ["bar", "line", "table", "kpi"];
  if (shape?.kind === "scatter") kinds.splice(2, 0, "scatter");
  return kinds;
}

export function kindLabel(kind: ChartKind): string {
  if (kind === "kpi") return "KPI";
  if (kind === "scatter") return "Scatter";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** Every served row. A missing category axis gets a display index, not a new measure. */
export function chartRows(rows: Record<string, unknown>[], view: ChartView): {
  rows: Record<string, unknown>[];
  x: string;
} {
  if ((view.kind === "bar" || view.kind === "line") && !view.x) {
    return {
      x: "_row",
      rows: rows.map((row, i) => ({ ...row, _row: String(i + 1) })),
    };
  }
  return { rows, x: view.x };
}

export function kpiValue(rows: Record<string, unknown>[], y: string): number | null {
  const cell = rows[0]?.[y];
  return typeof cell === "number" && Number.isFinite(cell) ? cell : null;
}

export function presentAnswer(
  text: string,
  rows: Record<string, unknown>[],
  values: FigureValue[],
): { mode: "keep"; text: string } | { mode: "summary"; lead: string; parts: SummaryPart[] } {
  const lead = stripListing(text, rows);
  if (lead == null) return { mode: "keep", text };
  return { mode: "summary", lead, parts: summaryParts(rows, values) };
}

export function summaryText(parts: SummaryPart[]): string {
  return parts.map((part) => part.text).join("");
}

export function summaryParts(rows: Record<string, unknown>[], values: FigureValue[]): SummaryPart[] {
  const cols = columnKinds(rows);
  const nums = names(cols, "numeric");
  const cats = names(cols, "category");
  const dates = names(cols, "date");
  const count = rows.length;
  if (!nums.length) return [{ kind: "text", text: `${count} rows.` }];
  const measure = nums[0];
  let best: Record<string, unknown> | null = null;
  let bestN: number | null = null;
  for (const row of rows) {
    const num = asNum(row[measure]);
    if (num == null) continue;
    if (bestN == null || num > bestN) {
      best = row;
      bestN = num;
    }
  }
  if (!best || bestN == null) return [{ kind: "text", text: `${count} rows.` }];
  const topShown = Number(bestN.toFixed(2));
  const topText = formatFigure(topShown, measure, values);
  const topId = figureId(values, measure, topShown);
  const nameCol = cats[0] ?? dates[0] ?? "";
  const rawName = nameCol ? best[nameCol] : "";
  const name = rawName == null || rawName === "" ? "" : String(rawName).trim();
  const total = columnSum(rows, measure);
  const parts: SummaryPart[] = [];
  const pushFigure = (text: string, id: string | null) => {
    if (id) parts.push({ kind: "figure", id, text });
    else parts.push({ kind: "text", text });
  };
  if (total == null || close(total, topShown)) {
    if (name) parts.push({ kind: "text", text: `${name} is ` });
    pushFigure(topText, topId);
    parts.push({ kind: "text", text: "." });
    return parts;
  }
  const totalText = formatFigure(total, measure, values);
  const totalId = figureId(values, measure, total);
  if (name) parts.push({ kind: "text", text: `${name} is highest at ` });
  else parts.push({ kind: "text", text: "Highest is " });
  pushFigure(topText, topId);
  parts.push({ kind: "text", text: ", total " });
  pushFigure(totalText, totalId);
  parts.push({ kind: "text", text: ` across ${count} rows.` });
  return parts;
}

export function figureFor(
  values: FigureValue[],
  column: string,
  raw: unknown,
): { id: string | null; text: string } | null {
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return { id: figureId(values, column, raw), text: formatFigure(raw, column, values) };
}

function figureId(values: FigureValue[], column: string, n: number): string | null {
  const labeled = values.find((item) => item.label === column && close(item.value, n));
  if (labeled) return labeled.id;
  return values.find((item) => close(item.value, n))?.id ?? null;
}

function columnSum(rows: Record<string, unknown>[], key: string): number | null {
  let total = 0;
  for (const row of rows) {
    const num = asNum(row[key]);
    if (num == null) return null;
    total += num;
  }
  if (!rows.length) return null;
  return Number(total.toFixed(2));
}

function stripListing(text: string, rows: Record<string, unknown>[]): string | null {
  if (!rows.length) return null;
  const kept: string[] = [];
  let saw = false;
  for (const line of text.split("\n")) {
    if (line === "") {
      kept.push(line);
      continue;
    }
    if (FOUND_LINE.test(line)) {
      saw = true;
      continue;
    }
    if (line.startsWith("  - ")) {
      if (!lineIsRow(line.slice(4), rows)) return null;
      saw = true;
      continue;
    }
    kept.push(line);
  }
  if (!saw) return null;
  return kept.join("\n").trim();
}

function lineIsRow(body: string, rows: Record<string, unknown>[]): boolean {
  const pairs: [string, string][] = [];
  for (const part of body.split(", ")) {
    const at = part.indexOf("=");
    if (at <= 0) return false;
    pairs.push([part.slice(0, at), part.slice(at + 1)]);
  }
  if (!pairs.length) return false;
  return rows.some((row) => pairs.every(([key, raw]) => cellMatches(row, key, raw)));
}

function cellMatches(row: Record<string, unknown>, key: string, raw: string): boolean {
  if (!Object.prototype.hasOwnProperty.call(row, key)) return false;
  const cell = row[key];
  if (String(cell) === raw) return true;
  const num = asNum(cell);
  const parsed = Number(raw);
  if (num == null || !Number.isFinite(parsed)) return false;
  return close(num, parsed);
}

function columnKinds(rows: Record<string, unknown>[]): { name: string; kind: ColumnKind }[] {
  const namesSeen: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (key && !namesSeen.includes(key)) namesSeen.push(key);
    }
  }
  return namesSeen.map((name) => ({ name, kind: columnKind(rows, name) }));
}

function names(cols: { name: string; kind: ColumnKind }[], kind: ColumnKind): string[] {
  return cols.filter((col) => col.kind === kind).map((col) => col.name);
}

function columnKind(rows: Record<string, unknown>[], key: string): ColumnKind {
  let kind: ColumnKind | null = null;
  for (const row of rows) {
    const value = row[key];
    if (value == null || value === "") continue;
    const next = valueKind(value);
    if (next === "other") return "other";
    if (kind && kind !== next) return "other";
    kind = next;
  }
  return kind ?? "other";
}

function valueKind(value: unknown): ColumnKind {
  if (typeof value === "number" && Number.isFinite(value)) return "numeric";
  if (typeof value === "string" && ISO_DATE.test(value.trim())) return "date";
  if (typeof value === "string" && value.trim()) return "category";
  return "other";
}

function asNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
