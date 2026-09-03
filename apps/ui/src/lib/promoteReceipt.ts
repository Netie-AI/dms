import type { PromoteReceipt } from "./api";

export type ReceiptDefect = {
  kind: "fanout" | "lost" | "unmeasured" | "mismatch";
  sentence: string;
};

/**
 * Direction of a conservation failure. Read `unmatched`, never
 * `counts_by_reason.join_cardinality_change` — promote.py:637-639 stores
 * abs(unmatched) there, so fan-out and row loss look the same in the list.
 */
export function describeReceiptDefect(receipt: PromoteReceipt): ReceiptDefect | null {
  if (receipt.reconciled) return null;
  if (receipt.unmatched < 0) {
    const n = -receipt.unmatched;
    return {
      kind: "fanout",
      sentence: `Join fan-out: ${n} more rows came out than went in.`,
    };
  }
  if (receipt.unmatched > 0) {
    return {
      kind: "lost",
      sentence: `${receipt.unmatched} rows did not arrive: they are in neither the target nor quarantine.`,
    };
  }
  if (receipt.source_rows === null) {
    return {
      kind: "unmeasured",
      sentence: "Cannot be reconciled: this run recorded no input count.",
    };
  }
  const d = Math.abs(receipt.passed + receipt.quarantined - receipt.source_rows);
  return {
    kind: "mismatch",
    sentence: `Row count does not add up: source_rows and passed + quarantined differ by ${d}.`,
  };
}
