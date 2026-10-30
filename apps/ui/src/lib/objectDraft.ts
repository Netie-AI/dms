/** Draft an objects.* publish from a landed bronze table (not only a fresh extract). */

export function asObjectIdent(raw: string, fallback = "object"): string {
  const t = raw.trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9_]/g, "");
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(t)) return t;
  if (t && /^[0-9]/.test(t)) return `c_${t}`;
  const fb = fallback.replace(/[^A-Za-z0-9_]/g, "_");
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(fb)) return fb;
  return `c_${fb || "object"}`;
}

const LAKE_REL = /^(bronze|silver|gold)\.[A-Za-z0-9_]+$/;

export function bronzeRelationFromPreview(
  target: { kind: "bronze" | "warehouse"; table: string } | null | undefined,
): string | null {
  if (!target || target.kind !== "bronze") return null;
  const t = target.table.trim();
  if (LAKE_REL.test(t)) return t;
  if (/^[A-Za-z0-9_]+$/.test(t)) return `bronze.${t}`;
  return null;
}

export function objectDraftFromBronze(
  relation: string,
  columns: string[],
  rows?: Record<string, unknown>[],
): { name: string; aliases: Record<string, string>; toolSql: string } {
  const stem = relation.includes(".") ? relation.split(".").slice(1).join(".") : relation;
  const name = asObjectIdent(stem);
  const aliases: Record<string, string> = {};
  for (const c of columns) {
    if (!c || c.startsWith("_")) continue;
    aliases[c] = c;
  }
  return {
    name,
    aliases,
    toolSql:
      inferGroupedToolSql(name, Object.keys(aliases), rows) ||
      `SELECT COUNT(*) AS row_count FROM objects.${name}`,
  };
}

function cellNumber(v: unknown): number | null {
  if (typeof v === "boolean") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return null;
}

/** GROUP BY a short category + SUM a numeric column so Publish-and-ask can chart. */
export function inferGroupedToolSql(
  objectName: string,
  columns: string[],
  rows: Record<string, unknown>[] | undefined,
): string | null {
  const ident = asObjectIdent(objectName);
  if (!ident || !rows?.length) return null;
  const cats: { src: string; sql: string }[] = [];
  const nums: string[] = [];
  for (const c of columns) {
    if (!c || c.startsWith("_")) continue;
    const sql = asObjectIdent(c, "");
    if (!sql) continue;
    const vals = rows.map((r) => r[c]).filter((v) => v != null && v !== "");
    if (!vals.length) continue;
    if (vals.every((v) => cellNumber(v) != null)) {
      if (!/^(id|.*_id)$/i.test(c)) nums.push(sql);
      continue;
    }
    if (/_at$/i.test(c) || /^(id|.*_id)$/i.test(c)) continue;
    const distinct = new Set(vals.map((v) => String(v)));
    if (distinct.size >= 2) cats.push({ src: c, sql });
  }
  if (!cats.length || !nums.length) return null;
  const picked =
    cats.find((col) => {
      const distinct = new Set(
        rows
          .map((r) => r[col.src])
          .filter((v) => v != null && v !== "")
          .map(String),
      );
      return distinct.size >= 2 && distinct.size <= 8;
    }) ?? cats[0];
  const cat = picked.sql;
  const num = nums[0];
  return (
    `SELECT ${cat}, SUM(CAST(${num} AS DOUBLE)) AS ${num} ` +
    `FROM objects.${ident} GROUP BY ${cat}`
  );
}