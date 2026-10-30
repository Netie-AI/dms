import { describe, expect, it } from "vitest";
import { describeActionReceipt } from "./actionReceipt";
import { healthyReceipt } from "./actionFixtures";
import type { ActionReceipt } from "./actions";

// ACT03_PROOF=1 (scripts/verify/act_03_ui_demo.py) prints the rendered strings.
function proof(label: string, value: unknown): void {
  if (process.env.ACT03_PROOF) {
    console.log(`[ACT03] ${label}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
  }
}

function receipt(patch: Partial<ActionReceipt>): ActionReceipt {
  return { ...healthyReceipt, ...patch };
}

describe("describeActionReceipt", () => {
  it("healthy: defect null and the one honest sentence", () => {
    const out = describeActionReceipt(healthyReceipt);
    proof("healthy receipt sentence", out.sentence);
    proof("healthy receipt defect", out.defect);
    expect(out.defect).toBeNull();
    expect(out.sentence).toBe(
      "Reorder level for SKU-BETA at WH-B: 400 (source) -> 450. Verified by re-read. Receipt e1 recorded in the ledger. Source system unchanged.",
    );
  });

  it("verified:false is 'unverified'", () => {
    const out = describeActionReceipt(receipt({ verified: false }));
    expect(out.defect).toBe("unverified");
    expect(out.sentence).not.toContain("Verified by re-read");
  });

  it("status other than applied, or rows_changed != 1, is 'unverified'", () => {
    expect(describeActionReceipt(receipt({ status: "rejected" })).defect).toBe("unverified");
    expect(describeActionReceipt(receipt({ rows_changed: 0 })).defect).toBe("unverified");
    expect(describeActionReceipt(receipt({ rows_changed: 2 })).defect).toBe("unverified");
  });

  it("empty ledger_entry_id is 'no_ledger_entry'", () => {
    expect(describeActionReceipt(receipt({ ledger_entry_id: "" })).defect).toBe("no_ledger_entry");
    expect(describeActionReceipt(receipt({ ledger_entry_id: null })).defect).toBe(
      "no_ledger_entry",
    );
  });

  it("equal hashes are 'hash_unchanged'", () => {
    const out = describeActionReceipt(
      receipt({ after_hash: healthyReceipt.before_hash }),
    );
    expect(out.defect).toBe("hash_unchanged");
  });

  it("mutation.executed false is 'not_executed'", () => {
    const out = describeActionReceipt(
      receipt({ mutation: { ...healthyReceipt.mutation, executed: false } }),
    );
    expect(out.defect).toBe("not_executed");
  });

  it("chain.ok false is 'chain_unverified'", () => {
    const out = describeActionReceipt(receipt({ chain: { ok: false, first_break: 3 } }));
    proof("chain.ok false", out);
    expect(out.defect).toBe("chain_unverified");
    expect(out.sentence).toContain("ledger chain could not be verified");
  });

  it("chain.ok null is 'chain_unproven'", () => {
    const out = describeActionReceipt(
      receipt({ chain: { ok: null, reason: "cortex_unavailable" } }),
    );
    expect(out.defect).toBe("chain_unproven");
    expect(out.sentence).not.toContain("Receipt e1 recorded in the ledger");
  });

  it("a missing chain is 'chain_unproven', never healthy", () => {
    const out = describeActionReceipt(receipt({ chain: undefined }));
    expect(out.defect).toBe("chain_unproven");
  });

  it("an override before-value reads '(override)', a clear reads back to source", () => {
    const out = describeActionReceipt(
      receipt({
        op: "clear",
        before: { value: "450", row_version: 1, source_value: "400" },
        after: { value: null, row_version: 2, source_value: "400" },
      }),
    );
    expect(out.defect).toBeNull();
    expect(out.sentence).toContain("450 (override) -> 400 (source)");
  });
});
