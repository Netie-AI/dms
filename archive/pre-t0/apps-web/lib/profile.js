export function formatTimestamp(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return String(iso);
  }
}

export function shortAuditId(id) {
  if (!id) return "—";
  const s = String(id);
  return s.length > 12 ? `${s.slice(0, 8)}…` : s;
}

/**
 * Collapse changelog JSONL entries into display rows for the CLEAN DATA table.
 * API entries: { rule_id, col, old_value, new_value, row_id, ... }
 */
export function aggregateChangelog(entries = []) {
  if (!Array.isArray(entries) || entries.length === 0) return [];

  const byKey = new Map();
  for (const e of entries) {
    const rule = e.rule_id || e.rule || e.action || e.type || "unknown";
    const field = e.col || e.field || "";
    const before = String(e.old_value ?? e.before ?? "");
    const after = String(e.new_value ?? e.after ?? "");
    const key = `${rule}|${field}|${before}|${after}`;
    const prev = byKey.get(key);
    if (prev) {
      prev.rows += 1;
    } else {
      byKey.set(key, { rule, field, before, after, rows: 1 });
    }
  }
  return Array.from(byKey.values()).sort((a, b) => b.rows - a.rows);
}

function _examplesAreCellValues(examples) {
  if (!examples.length) return false;
  // Profiler summary tokens look like "kg:4164" / "iso:2759" / "null:1016"
  return !examples.every((e) => /^[a-z_]+:\d+$/i.test(String(e).trim()));
}

function _cellLooksMessy(issueType, val) {
  const v = String(val ?? "");
  if (issueType === "HIGH_NULL_RATE") return v.trim() === "";
  if (issueType === "UNIT_INCONSISTENCY") {
    return /[a-zA-Z]/.test(v) || v.trim() === "";
  }
  if (issueType === "FORMAT_INCONSISTENCY") {
    // highlight non-ISO dates and odd SKU forms when we only have summary examples
    return v.trim() !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(v);
  }
  if (issueType === "TYPE_CHAOS") {
    return v.trim() === "" || Number.isNaN(Number(v.replace(/[^\d.-]/g, "")));
  }
  // CATEGORICAL_VARIANTS / DUPLICATES / default: mark non-empty cells in issue col
  return v.trim() !== "";
}

/**
 * Build a Set of cell keys `${rowIndex}|${col}|${val}` for messy-cell highlighting.
 */
export function buildMessyHighlights(profile, rows = []) {
  const set = new Set();
  if (!profile?.detected_issues?.length || !Array.isArray(rows) || rows.length === 0) {
    return set;
  }

  for (const issue of profile.detected_issues) {
    const col = issue.col;
    if (!col || String(col).includes("+")) continue; // skip composite keys e.g. sku+location_id
    const issueType = issue.issue_type || "";
    const examples = (issue.examples || []).map(String).filter(Boolean);
    const useExamples = _examplesAreCellValues(examples);
    const exampleSet = useExamples ? new Set(examples) : null;

    rows.forEach((row, i) => {
      if (!(col in row)) return;
      const val = String(row[col] ?? "");
      const match = exampleSet
        ? exampleSet.has(val)
        : _cellLooksMessy(issueType, val);
      if (match) set.add(`${i}|${col}|${val}`);
    });
  }
  return set;
}
