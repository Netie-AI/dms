const KEYWORDS = ["SELECT", "FROM", "WHERE", "JOIN", "ORDER", "BY", "GROUP", "HAVING", "LIMIT", "AND", "OR", "AS", "ON", "INNER", "LEFT", "RIGHT"];

export function highlightSql(sql) {
  if (!sql) return "";
  let out = sql
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  for (const kw of KEYWORDS) {
    const re = new RegExp(`\\b(${kw})\\b`, "gi");
    out = out.replace(re, '<span class="cx-sql-kw">$1</span>');
  }
  return out;
}
