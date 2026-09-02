import type { AnswerEnvelope, ContributingSource } from "./types";

const FROM_TABLE = /\bfrom\s+((?:[A-Za-z_][\w]*|"[^"]+")(?:\.(?:[A-Za-z_][\w]*|"[^"]+"))?)/i;

function kindForTable(name: string): ContributingSource["kind"] {
  const lower = name.toLowerCase();
  if (lower.includes("xlsx") || lower.startsWith("bronze.")) return "xlsx";
  return "sql";
}

export function tablesFromSql(sql: string | undefined): string[] {
  if (!sql) return [];
  const m = FROM_TABLE.exec(sql);
  if (!m) return [];
  return [m[1].replace(/"/g, "")];
}

/** Cards for the docked Sources pane. Never invent Cortex provenance. */
export function sourcesForPanel(env: AnswerEnvelope | null): ContributingSource[] {
  if (!env) return [];
  const attached = env.contributing_sources ?? [];
  if (attached.length) return attached;
  const names = [...new Set([...(env.grounded_tables ?? []), ...tablesFromSql(env.sql_used)])];
  if (!names.length) return [];
  const n = names.length;
  return names.map((name, i) => ({
    ref_id: `scope_${i}_${name}`,
    container: name,
    kind: kindForTable(name),
    row_count: env.rows?.length ?? 0,
    contribution: 1 / n,
  }));
}

export function sourcesHeadline(
  env: AnswerEnvelope | null,
  sources: ContributingSource[],
  totalRows: number,
): string {
  if (!env) return "No answer yet - ask from Chat";
  if (sources.length) {
    return `${sources.length} ${sources.length === 1 ? "table" : "tables"} · ${totalRows.toLocaleString()} rows`;
  }
  return "Certified answer, no file card from Cortex. Open SQL.";
}
