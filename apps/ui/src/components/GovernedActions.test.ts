import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { GovernedActions, createLatestGate } from "./GovernedActions";
import { healthyReceipt } from "../lib/actionFixtures";
import { decisionText } from "../lib/actionReceipt";
import type { ActionDecision, ActionReceipt } from "../lib/actions";

const decision: ActionDecision = {
  id: healthyReceipt.decision_id,
  space_id: "S",
  proposal_id: healthyReceipt.proposal_id,
  proposal_version_id: "v1",
  decision: "applied",
  action_type: healthyReceipt.action_type,
  object_type: "inventory_item",
  object_key: { sku: "SKU-BETA", location_id: "WH-B" },
  field: "reorder_level_kg",
  op: "set",
  before: healthyReceipt.before,
  after: healthyReceipt.after,
  before_hash: healthyReceipt.before_hash,
  after_hash: healthyReceipt.after_hash,
  rows_changed: 1,
  verified: true,
  approval_ref: "CHG-1",
  evidence: null,
  cortex_entry_id: "e1",
  ledger_seq: 7,
  decided_at: "2026-10-31T01:00:00Z",
};

const unprovenReceipt: ActionReceipt = {
  ...healthyReceipt,
  chain: { ok: null, reason: "chain_read_failed" },
};

function render(receipts: ActionReceipt[], decisions: ActionDecision[] = [decision]): string {
  return renderToStaticMarkup(
    createElement(GovernedActions, { spaceId: null, seed: { decisions, receipts } }),
  );
}

function statusCells(html: string): string[] {
  return html
    .split('data-testid="action-decision-status">')
    .slice(1)
    .map((s) => s.slice(0, s.indexOf("</td>")));
}

describe("Recent decisions: chain caveat", () => {
  it("an applied row next to a chain_unproven receipt carries the caveat", () => {
    const html = render([unprovenReceipt]);
    expect(statusCells(html)).toEqual(["applied (ledger chain not read back)"]);
    // The receipt panel above says unproven too; the two now agree.
    expect(html).toContain("so the receipt is unproven");
  });

  it("chain_unverified on the receipt reads 'ledger chain did not verify'", () => {
    const html = render([{ ...healthyReceipt, chain: { ok: false, first_break: 3 } }]);
    expect(statusCells(html)).toEqual(["applied (ledger chain did not verify)"]);
  });

  it("a healthy receipt leaves the row as plain 'applied'", () => {
    expect(statusCells(render([healthyReceipt]))).toEqual(["applied"]);
  });

  it("every Recent decisions table says a row is not by itself a ledger proof", () => {
    const html = render([]);
    expect(html).toContain(
      "Each row is what the DMS database recorded. The ledger chain is read back only on an apply receipt, so a row is not by itself a ledger proof.",
    );
    expect(statusCells(html)).toEqual(["applied"]);
  });

  it("unverified and entry-less rows say so; declined rows are unchanged", () => {
    expect(decisionText({ ...decision, verified: false })).toBe("applied (not verified)");
    expect(decisionText({ ...decision, cortex_entry_id: null })).toBe("applied (no ledger entry)");
    expect(decisionText({ ...decision, decision: "declined" })).toBe("declined");
    expect(
      decisionText({ ...decision, cortex_entry_id: null }, [{ ...healthyReceipt, ledger_entry_id: null }]),
    ).toBe("applied (no ledger entry)");
  });
});

describe("Space switch race", () => {
  it("only the latest load may write: a slow answer for the old Space is dropped", async () => {
    const gate = createLatestGate();
    const shown: string[] = [];
    let resolveA: (v: string) => void = () => {};
    const slowA = new Promise<string>((r) => {
      resolveA = r;
    });
    const isA = gate.begin();
    const loadA = slowA.then((cards) => {
      if (isA()) shown.push(cards);
    });
    // Switch to Space B before A answers.
    const isB = gate.begin();
    await Promise.resolve("B cards").then((cards) => {
      if (isB()) shown.push(cards);
    });
    resolveA("A cards");
    await loadA;
    expect(shown).toEqual(["B cards"]);
    expect(isA()).toBe(false);
    expect(isB()).toBe(true);
  });
});
