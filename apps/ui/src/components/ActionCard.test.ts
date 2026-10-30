import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ActionCard, ActionCardView } from "./ActionCard";
import { pendingProposal } from "../lib/actionFixtures";
import type { RefusalInfo } from "../lib/actions";

// ACT03_PROOF=1 (scripts/verify/act_03_ui_demo.py) prints the rendered strings.
function proof(label: string, value: unknown): void {
  if (process.env.ACT03_PROOF) {
    console.log(`[ACT03] ${label}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
  }
}

function view(approvalRef: string, refusal: RefusalInfo | null = null): string {
  return renderToStaticMarkup(
    createElement(ActionCardView, {
      proposal: pendingProposal,
      approvalRef,
      onApprovalRefChange: () => {},
      busy: false,
      refusal,
      onApply: () => {},
      onDecline: () => {},
    }),
  );
}

function applyButton(html: string): string {
  const m = html.match(/<button[^>]*data-testid="action-apply"[^>]*>/);
  expect(m).not.toBeNull();
  return m![0];
}

describe("ActionCard", () => {
  it("diff row: Object | Field | Source | Current override | Proposed", () => {
    const html = view("");
    for (const head of ["Object", "Field", "Source", "Current override", "Proposed"]) {
      expect(html).toContain(`>${head}</th>`);
    }
    const row = html.split('data-testid="action-diff-row"')[1] ?? "";
    expect(row).toContain("SKU-BETA");
    expect(row).toContain("WH-B");
    expect(row).toContain("reorder_level_kg");
    expect(row).toContain('data-testid="diff-source">400');
    expect(row).toContain('data-testid="diff-current">—');
    expect(row).toContain('data-testid="diff-proposed">450');
  });

  it("reason and source are shown", () => {
    const html = view("");
    expect(html).toContain("Below reorder level on the cited answer");
    expect(html).toContain("Source: demo warehouse");
  });

  it("Apply is disabled until the approval reference has 3 characters", () => {
    expect(applyButton(view(""))).toContain(" disabled=\"\"");
    expect(applyButton(view("ab"))).toContain(" disabled=\"\"");
    expect(applyButton(view("  ab "))).toContain(" disabled=\"\"");
    expect(applyButton(view("CAB-1"))).not.toContain(" disabled=\"\"");
  });

  it("the stateful card starts with an empty reference and Apply disabled", () => {
    const html = renderToStaticMarkup(
      createElement(ActionCard, { proposal: pendingProposal, onDone: () => {} }),
    );
    expect(applyButton(html)).toContain(" disabled=\"\"");
    expect(html).toContain('data-testid="action-approval-ref"');
    expect(html).toContain("required");
  });

  it("object_key_not_found refusal offers the candidate", () => {
    const html = view("CAB-1", {
      code: "object_key_not_found",
      detail: {
        code: "object_key_not_found",
        object_key: { sku: "BETA", location_id: "WH-B" },
        candidates: [{ sku: "SKU-BETA", location_id: "WH-B" }],
      },
    });
    proof("object_key_not_found refusal", html.split('data-testid="action-refusal"')[1]?.split(">")[1]?.split("<")[0]);
    expect(html).toContain('data-testid="action-refusal"');
    expect(html).toContain("Did you mean SKU-BETA?");
  });

  it("object_changed refusal names the current value and never says Applied", () => {
    const html = view("CAB-1", {
      code: "object_changed",
      detail: { code: "object_changed", current_value: "450", current_row_version: 1 },
    });
    proof("object_changed refusal contains 'Applied'", html.includes("Applied"));
    expect(html).toContain("Value changed to 450 since this was proposed.");
    expect(html).not.toContain("Applied");
  });

  it("the approval reference field asks for a change ticket id", () => {
    expect(view("")).toContain('placeholder="Change ticket id, e.g. CHG-1234 (required)"');
  });

  // The ACT-02 round-4 refusals, with the detail the route sends.
  it("approval_ref_invalid refusal says what a ref must be and that nothing was written", () => {
    const html = view("CHG-900101-14-5678", {
      code: "approval_ref_invalid",
      detail: { code: "approval_ref_invalid", max: 200 },
    });
    const text = html.split('data-testid="action-refusal"')[1]?.split(">")[1]?.split("<")[0];
    proof("approval_ref_invalid refusal", text);
    expect(text).toBe(
      "The approval reference must be one ticket or change id (letters, digits and - _ . / : #, with at least one letter), not an email, a sentence, a bare number, or one that contains an NRIC or card number. Nothing was written.",
    );
    expect(html).not.toContain("Applied");
  });

  it("record_failed_after_append refusal names the voided entry and never says Applied", () => {
    const html = view("CAB-1", {
      code: "record_failed_after_append",
      detail: { code: "record_failed_after_append", cortex_entry_id: "e1" },
    });
    const text = html.split('data-testid="action-refusal"')[1]?.split(">")[1]?.split("<")[0];
    proof("record_failed_after_append refusal", text);
    expect(text).toBe(
      "Ledger entry e1 was written but the database failed before the decision was recorded; a void entry was requested and nothing changed.",
    );
    expect(html).not.toContain("Applied");
  });
});
