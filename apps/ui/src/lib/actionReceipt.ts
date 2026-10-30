import type { ActionReceipt, CellState, ObjectKey } from "./actions";

export type ActionReceiptDefect =
  | "unverified"
  | "not_executed"
  | "no_ledger_entry"
  | "hash_unchanged"
  | "chain_unverified"
  | "chain_unproven";

const FIELD_LABEL: Record<string, string> = {
  reorder_level_kg: "Reorder level",
};

export function fieldLabel(field: string): string {
  return FIELD_LABEL[field] ?? field;
}

export function objectText(key: ObjectKey | undefined | null): string {
  if (!key) return "";
  const sku = key.sku ?? "";
  const loc = key.location_id ?? "";
  if (sku && loc) return `${sku} at ${loc}`;
  return Object.values(key).join(" @ ");
}

/** '400 (source)' when no override exists, '450 (override)' when one does. */
export function cellText(cell: CellState | null | undefined): string {
  if (!cell) return "unknown";
  if (cell.value != null) return cell.value;
  return `${cell.source_value ?? "none"} (source)`;
}

function beforeText(cell: CellState | null | undefined): string {
  if (cell && cell.value != null) return `${cell.value} (override)`;
  return cellText(cell);
}

/**
 * Whether an apply receipt proves its change, and the sentence to say.
 *
 * ``defect`` is null only when every one of these holds: status 'applied',
 * verified, exactly one row changed, the server says the write executed, a
 * non-empty ledger entry id, before_hash != after_hash, and the ledger chain
 * read back ok. Anything less is a defect and the panel must not say "Applied".
 * Mirrors lib/promoteReceipt.describeReceiptDefect.
 */
export function describeActionReceipt(r: ActionReceipt): {
  sentence: string;
  defect: ActionReceiptDefect | null;
} {
  const what = `${fieldLabel(r.field)} for ${objectText(r.object?.object_key)}`;
  const change = `${what}: ${beforeText(r.before)} -> ${cellText(r.after)}`;

  if (r.status !== "applied" || r.verified !== true || r.rows_changed !== 1) {
    return {
      defect: "unverified",
      sentence: `${what}: the change was not verified by re-read (status ${r.status}, ${r.rows_changed} ${r.rows_changed === 1 ? "row" : "rows"} changed). Treat it as not made.`,
    };
  }
  if (r.mutation?.executed !== true) {
    return {
      defect: "not_executed",
      sentence: `${what}: the server reports no write was executed.`,
    };
  }
  if (!r.ledger_entry_id) {
    return {
      defect: "no_ledger_entry",
      sentence: `${change}. No ledger entry was returned, so there is no receipt for this change.`,
    };
  }
  if (!r.before_hash || r.before_hash === r.after_hash) {
    return {
      defect: "hash_unchanged",
      sentence: `${what}: before and after hashes are equal, so nothing provably changed.`,
    };
  }
  const ok = r.chain?.ok;
  if (ok === false) {
    return {
      defect: "chain_unverified",
      sentence: `${change}. Entry ${r.ledger_entry_id} was written, but the ledger chain could not be verified (first break ${String(r.chain?.first_break ?? "unknown")}).`,
    };
  }
  if (ok !== true) {
    return {
      defect: "chain_unproven",
      sentence: `${change}. Entry ${r.ledger_entry_id} was written, but the ledger chain was not read back (${r.chain?.reason ?? "no readback"}), so the receipt is unproven.`,
    };
  }
  const source =
    r.mutation.source_system_changed === false
      ? " Source system unchanged."
      : " The source system was not confirmed unchanged.";
  return {
    defect: null,
    sentence: `${change}. Verified by re-read. Receipt ${r.ledger_entry_id} recorded in the ledger.${source}`,
  };
}
