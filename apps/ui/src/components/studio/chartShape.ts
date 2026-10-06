import type { ChartSpec } from "@/lib/types";

/** ISO date or datetime. Year-only is not a date. */
const ISO_DATE =
  /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

function isNumeric(v: unknown): boolean {
  return typeof v === "number" && Number.isFinite(v);
}

function isDate(v: unknown): boolean {
  return typeof v === "string" && ISO_DATE.test(v.trim());
}

function isCategory(v: unknown): boolean {
  return typeof v === "string" && v.trim() !== "" && !isDate(v);
}

type ColKind = "numeric" | "date" | "category" | "other";

function columnKind(rows: Record<string, unknown>[], key: string): ColKind {
  let kind: ColKind | null = null;
  for (const row of rows) {
    const v = row[key];
    if (v == null || v === "") continue;
    const next: ColKind = isNumeric(v)
      ? "numeric"
      : isDate(v)
        ? "date"
        : isCategory(v)
          ? "category"
          : "other";
    if (next === "other") return "other";
    if (kind && kind !== next) return "other";
    kind = next;
  }
  return kind ?? "other";
}

/**
 * Category + numeric = bar. Date + numeric = line.
 * Date wins when both a date column and a category column are present.
 * Anything else is not chartable. Does not read envelope.chart.
 */
export function chartFromRows(rows: Record<string, unknown>[]): ChartSpec | null {
  if (!rows.length) return null;
  const keys = Object.keys(rows[0]);
  let numeric: string | null = null;
  let date: string | null = null;
  let category: string | null = null;
  for (const key of keys) {
    const kind = columnKind(rows, key);
    if (kind === "numeric" && !numeric) numeric = key;
    else if (kind === "date" && !date) date = key;
    else if (kind === "category" && !category) category = key;
  }
  if (date && numeric) return { kind: "line", x: date, y: numeric, title: "Result" };
  if (category && numeric) return { kind: "bar", x: category, y: numeric, title: "Result" };
  return null;
}
