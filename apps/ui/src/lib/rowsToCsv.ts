/** CSV-01 — serialize answer rows. Pure: no clock, no locale, no model. */

const UTF8_BOM = "\uFEFF";

export function csvEscape(value: string): string {
  if (/[",\n\r]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

function csvNumber(n: number): string {
  if (Object.is(n, -0)) return "0";
  if (!Number.isFinite(n)) return String(n);
  return JSON.stringify(n);
}

function srcRef(item: unknown): string | null {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const o = item as Record<string, unknown>;
  const ref = o.ref_id ?? o.ref ?? "";
  const row = o.row ?? o.line ?? "";
  if (ref === "" || row === "") return null;
  return `${ref}:${row}`;
}

export function csvCell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number") return csvNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    const refs = value.map(srcRef);
    if (refs.every((r) => r != null)) return refs.join(", ");
    return value.map((item) => csvCell(item)).join(", ");
  }
  if (typeof value === "object") {
    const ref = srcRef(value);
    if (ref) return ref;
    try {
      return JSON.stringify(value);
    } catch {
      return "[unserializable]";
    }
  }
  return String(value);
}

export function columnOrder(rows: Record<string, unknown>[]): string[] {
  const cols: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (seen.has(key)) continue;
      seen.add(key);
      cols.push(key);
    }
  }
  return cols;
}

export function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return UTF8_BOM;
  const cols = columnOrder(rows);
  const lines = [
    cols.map(csvEscape).join(","),
    ...rows.map((row) => cols.map((c) => csvEscape(csvCell(row[c]))).join(",")),
  ];
  return `${UTF8_BOM}${lines.join("\r\n")}\r\n`;
}

export function csvDownloadName(answerId: string): string {
  const safe = answerId.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 80);
  return `dms_answer_${safe || "export"}.csv`;
}

/** One aggregate cell is a summary, not the detail table the download should offer. */
export function isSummaryExport(rows: Record<string, unknown>[]): boolean {
  if (rows.length !== 1) return false;
  const keys = Object.keys(rows[0]);
  if (keys.length !== 1) return false;
  return /count|total|sum|avg|average|min|max|revenue|value|metric/.test(keys[0].toLowerCase());
}
