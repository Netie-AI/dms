/**
 * Test fixtures for the governed-action UI (ACT-03). Shapes copy the ACT-02
 * route responses: the propose diff (dms_core.control_plane.actions.propose_action)
 * and the apply receipt (dms_api.routes.actions.apply_action_route).
 */
import type { ActionDiff, ActionProposal, ActionReceipt } from "./actions";

export const proposedDiff: ActionDiff = {
  kind: "action",
  action_type: "inventory.set_reorder_level",
  object_key: { sku: "SKU-BETA", location_id: "WH-B" },
  value: "450",
  op: "set",
  reason: "Below reorder level on the cited answer",
  object_type: "inventory_item",
  field: "reorder_level_kg",
  before: { value: null, row_version: 0, source_value: "400" },
  expected_before_hash: "764b89e2f2950000000000000000000000000000000000000000000000000000",
  expected_after_hash: "2008d61a81b60000000000000000000000000000000000000000000000000000",
  spec_sha: "5a5a5a5a5a5a",
  source_kind: "demo_warehouse",
  evidence: {
    audit_id: "led_ask_1",
    question: "Which SKUs are below reorder level?",
    badge: "validated",
    row_count: 2,
  },
  summary:
    "Set reorder_level_kg for inventory_item SKU-BETA @ WH-B: 400 (source) -> 450",
};

export const pendingProposal: ActionProposal = {
  proposal_id: "11111111-1111-4111-8111-111111111111",
  version_num: 1,
  status: "pending_confirm",
  idempotency_token: "tok_pending_1",
  diff: proposedDiff,
  created_at: "2026-10-30T01:00:00Z",
};

export const healthyReceipt: ActionReceipt = {
  decision_id: "22222222-2222-4222-8222-222222222222",
  proposal_id: pendingProposal.proposal_id,
  version_num: 1,
  status: "applied",
  action_type: "inventory.set_reorder_level",
  object: { object_type: "inventory_item", object_key: { sku: "SKU-BETA", location_id: "WH-B" } },
  field: "reorder_level_kg",
  op: "set",
  before: { value: null, row_version: 0, source_value: "400" },
  after: { value: "450", row_version: 1, source_value: "400" },
  before_hash: "764b89e2f2950000000000000000000000000000000000000000000000000000",
  after_hash: "2008d61a81b60000000000000000000000000000000000000000000000000000",
  verified: true,
  rows_changed: 1,
  approval_ref: "DEMO-3",
  evidence: {
    audit_id: "led_ask_1",
    question: "Which SKUs are below reorder level?",
    badge: "validated",
    row_count: 2,
  },
  identity: "config_actor (DR-0004 Option A)",
  separation_of_duties: { proven: false, reason: "one configured identity proposes and applies" },
  ledger_entry_id: "e1",
  ledger_seq: 7,
  chain: { ok: true, first_break: null },
  source_kind: "demo_warehouse",
  summary: proposedDiff.summary,
  mutation: {
    executed: true,
    target: "dms.action_writeback",
    source_system_changed: false,
    detail:
      "One row in a DMS-owned writeback table changed and was re-read. No customer source system or Excel file was written.",
  },
};
