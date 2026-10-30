import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ActionReceiptPanel } from "./ActionReceiptPanel";
import { healthyReceipt } from "../lib/actionFixtures";
import type { ActionReceipt } from "../lib/actions";

// ACT03_PROOF=1 (scripts/verify/act_03_ui_demo.py) prints the rendered strings.
function proof(label: string, value: unknown): void {
  if (process.env.ACT03_PROOF) {
    console.log(`[ACT03] ${label}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
  }
}

function text(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function render(receipt: ActionReceipt, onRevert?: () => void): string {
  return renderToStaticMarkup(createElement(ActionReceiptPanel, { receipt, onRevert }));
}

describe("ActionReceiptPanel", () => {
  it("healthy: Applied headline, before/after, ledger, source unchanged", () => {
    const html = render(healthyReceipt);
    proof("healthy panel text", text(html));
    expect(html).toContain('data-testid="action-receipt"');
    expect(html).toContain("Applied - 1 row in DMS writeback changed and re-read");
    expect(html).toContain('data-testid="action-before">400 (source)');
    expect(html).toContain('data-testid="action-after">450');
    expect(html).toContain('data-testid="action-ledger"');
    expect(html.split('data-testid="action-ledger"')[1]).toContain("e1");
    expect(html).toContain('data-testid="action-source-unchanged"');
    expect(html).toContain('data-testid="action-hashes">764b89e2f295 -&gt; 2008d61a81b6');
    expect(html).not.toContain('data-testid="action-defect"');
  });

  it("chain_unproven: defect shown, never 'Applied'", () => {
    const html = render({ ...healthyReceipt, chain: { ok: null, reason: "cortex_unavailable" } });
    proof("chain_unproven panel text", text(html));
    proof("chain_unproven panel contains 'Applied'", html.includes("Applied"));
    expect(html).toContain('data-testid="action-defect"');
    expect(html).not.toContain("Applied");
  });

  it("verified:false: defect shown, never 'Applied'", () => {
    const html = render({ ...healthyReceipt, verified: false });
    proof("verified:false panel text", text(html));
    proof("verified:false panel contains 'Applied'", html.includes("Applied"));
    expect(html).toContain('data-testid="action-defect"');
    expect(html).not.toContain("Applied");
  });

  it("chain_unverified and no_ledger_entry: defect shown, never 'Applied'", () => {
    for (const r of [
      { ...healthyReceipt, chain: { ok: false, first_break: 2 } },
      { ...healthyReceipt, ledger_entry_id: "" },
      { ...healthyReceipt, after_hash: healthyReceipt.before_hash },
      { ...healthyReceipt, mutation: { ...healthyReceipt.mutation, executed: false } },
    ]) {
      const html = render(r);
      expect(html).toContain('data-testid="action-defect"');
      expect(html).not.toContain("Applied");
    }
  });

  it("evidence names the cited governed answer", () => {
    const html = render(healthyReceipt);
    expect(html).toContain('data-testid="action-evidence"');
    expect(html).toContain(
      "Cited answer: Which SKUs are below reorder level? - validated, 2 rows",
    );
  });

  it("no cited answer says so", () => {
    const html = render({ ...healthyReceipt, evidence: {} });
    expect(html).toContain("No cited answer");
  });

  it("same configured identity is disclosed, never a second person", () => {
    const html = render(healthyReceipt);
    expect(html).toContain('data-testid="action-sod"');
    expect(html).toContain("Proposed and applied by the same configured identity");
  });

  it("revert proposes, it does not apply", () => {
    const html = render(healthyReceipt, () => {});
    expect(html).toContain('data-testid="action-revert"');
    expect(html).toContain("Propose revert");
    expect(html).toContain("creates a new proposal; nothing changes until it is applied");
  });
});
