/** Plugin-shaped delivery: share envelope, mock Space id, reconcile row totals. */

import type { AnswerEnvelope } from "./types";

export const MOCK_WAREHOUSE_OPS_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
export const MOCK_WAREHOUSE_OPS_NAME = "Warehouse Ops";
export const MOCK_STOCK_ASK = "What is total stock value by category?";

export function shareSpacePayload(space: { id: string; name: string }): string {
  return JSON.stringify(
    {
      kind: "dms.space",
      space_id: space.id,
      name: space.name,
      ask_path: "/",
      note: "Paste space_id into the Space switcher, or Open chat from Spaces.",
    },
    null,
    2,
  );
}

export function shareEnvelopePayload(envelope: AnswerEnvelope): string {
  return JSON.stringify(
    {
      kind: "dms.answer",
      answer_id: envelope.answer_id,
      badge: envelope.badge,
      abstained: envelope.abstained ?? envelope.badge === "ABSTAIN",
      text: envelope.text,
      values: envelope.values,
      rows: envelope.rows ?? [],
      chart: envelope.chart ?? null,
      space_id: envelope.space_id ?? null,
      audit_id: envelope.audit_id ?? null,
      as_of: envelope.as_of,
    },
    null,
    2,
  );
}

function numericKeys(rows: Record<string, unknown>[]): string[] {
  if (!rows.length) return [];
  const keys = Object.keys(rows[0]);
  return keys.filter((k) =>
    rows.every((r) => {
      const v = r[k];
      return v == null || typeof v === "number" || (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v)));
    }),
  );
}

function toNum(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * Accuracy-check a single answer against its own rows — not a fake percent.
 * Prefer chart.y; else the first numeric column that is not an id-like key.
 */
export function checkAnswerTotals(envelope: AnswerEnvelope): {
  status: "ok" | "mismatch" | "skip";
  message: string;
  rowSum?: number;
  stated?: number;
  column?: string;
} {
  const rows = envelope.rows ?? [];
  if (!rows.length) {
    return { status: "skip", message: "No rows to reconcile - open Trust for corpus accuracy." };
  }
  if (envelope.badge === "ABSTAIN" || envelope.abstained) {
    return { status: "skip", message: "Abstained - nothing to check; that is the correct outcome." };
  }

  const candidates = numericKeys(rows).filter(
    (k) => !/^(id|rank|n|count|index)$/i.test(k) && !/_id$/i.test(k),
  );
  const preferred = envelope.chart?.y && candidates.includes(envelope.chart.y)
    ? envelope.chart.y
    : candidates.find((k) => /value|amount|revenue|total|myr|usd|qty|quantity|stock/i.test(k)) ??
      candidates[0];

  if (!preferred) {
    return { status: "skip", message: "No numeric column to sum - download CSV and check in Excel." };
  }

  let rowSum = 0;
  for (const r of rows) {
    const n = toNum(r[preferred]);
    if (n == null) {
      return { status: "skip", message: `Column ${preferred} is not fully numeric.` };
    }
    rowSum += n;
  }

  const valueNums = envelope.values
    .map((v) => (typeof v.value === "number" && Number.isFinite(v.value) ? v.value : null))
    .filter((n): n is number => n != null);

  if (valueNums.length === 0) {
    return {
      status: "ok",
      message: `Row sum of ${preferred} = ${rowSum.toLocaleString(undefined, { maximumFractionDigits: 2 })} (no single stated total in values[] - compare CSV yourself).`,
      rowSum,
      column: preferred,
    };
  }

  const valueSum = valueNums.reduce((a, b) => a + b, 0);
  const tol = Math.max(0.01, Math.abs(rowSum) * 1e-9);

  // One stated figure: it must be the grand total, not a lucky first row.
  if (valueNums.length === 1) {
    const stated = valueNums[0];
    if (Math.abs(rowSum - stated) <= Math.max(0.01, Math.abs(stated) * 1e-9)) {
      return {
        status: "ok",
        message: `Match - rows sum to ${rowSum.toLocaleString(undefined, { maximumFractionDigits: 2 })} (= stated ${stated.toLocaleString(undefined, { maximumFractionDigits: 2 })}).`,
        rowSum,
        stated,
        column: preferred,
      };
    }
    return {
      status: "mismatch",
      message: `Mismatch - row sum ${rowSum.toLocaleString(undefined, { maximumFractionDigits: 2 })} vs stated ${stated.toLocaleString(undefined, { maximumFractionDigits: 2 })} on ${preferred}.`,
      rowSum,
      stated,
      column: preferred,
    };
  }

  // Grouped ranking: values[] are per-row, not a grand total. Sum must match.
  if (Math.abs(rowSum - valueSum) <= tol) {
    return {
      status: "ok",
      message: `Match - ${valueNums.length} grouped values sum to ${valueSum.toLocaleString(undefined, { maximumFractionDigits: 2 })} (= row sum).`,
      rowSum,
      stated: valueSum,
      column: preferred,
    };
  }
  return {
    status: "mismatch",
    message: `Mismatch - row sum ${rowSum.toLocaleString(undefined, { maximumFractionDigits: 2 })} vs values[] sum ${valueSum.toLocaleString(undefined, { maximumFractionDigits: 2 })} on ${preferred}.`,
    rowSum,
    stated: valueSum,
    column: preferred,
  };
}
