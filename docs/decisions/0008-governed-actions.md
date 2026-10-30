---
status: proposed
date: 2026-10-29
decision-makers: founder (stamp required) - proposed by the Claude Code ACTIONS lane, ticket ACT-01
outcome: pending founder stamp
---

# DR-0008 - Governed actions write one DMS-owned table, and a steward confirms every apply

Numbering: DR-0006 is claimed by both #440 and #461, so whichever of those merges second
moves to DR-0007. DR-0007 is reserved for that renumber. This lane takes DR-0008; the
INSIGHTS-PROOF lane takes DR-0009. Checked at origin/main `58fcaa5` and across the remote
branches.

## Context and Problem Statement

The founder's direction on 2026-10-29 was to build "the best AI, DB-GPT / Palantir
driven, with actions, best solutions and business insights with proof". An answer that
stops at a number is half of that. The other half is an *action*: the person reads that
SKU-BETA at WH-B is below cover, and the product lets a steward change the reorder level
there, with a receipt.

Founder amendment text: the brief that carried this ticket left the amendment text
empty (`""`). The founder's own words for this request are:

> While keeping this run turned on scale and build all sessions together towards
> perfectness work with all different session uc may escalate call more sseions and work
> towards the best ai dbgpt paantir driven ai, with actions and a best solutiosn and
> business isnights with proof

That is a direction, not an amendment. Whether PRD-001 gains an "actions" section, and in
which epic, is the PRD Agent's and the founder's call. Until it is stamped, everything
below is behind `DMS_ACTIONS`, which defaults off, and nothing on main calls it.

A Palantir-style action writes back. The risk is that it writes somewhere it must not:
a customer's SQL Server, a customer's Excel file (hard rule 5), or a ledger of its own
(hard rule 3). The second risk is an action that applies itself.

## Decision (proposed)

1. **One typed action type, declared in code.** `inventory.set_reorder_level` on
   `inventory_item`, keys `(sku, location_id)`, field `reorder_level_kg`, range
   0..10,000,000. A request supplies a spec (action, key, value, `set` or `clear`,
   reason). It never supplies a table, column or SQL. New types are code changes.
2. **Writes go to `dms.action_writeback` only.** A DMS-owned overlay table: one cell per
   (tenant, object type, object key, field), a canonical Decimal string, NULL meaning "no
   override, the source applies", and a `row_version` that bumps on every write. A
   customer source system and an Excel file are never written. There is no DELETE grant.
3. **No auto-apply (no L3).** Every apply needs a human steward and an `approval_ref` of
   at least three characters. The plain amend confirm refuses an action version
   (`ActionRequiresApply`), so no other path can flip one to `applied`.
4. **Apply proves itself.** The confirm runs under the proposal advisory lock, which is
   released when the confirm returns; the precondition and the write are serialised by
   the row locks (`FOR UPDATE` on the version and the cell). The apply checks the stored
   diff against its token, the pinned spec digest and the pinned whole-diff digest and
   version id, and recomputes the expected hashes and summary from the spec. It then
   checks that the cell is still in the state the proposal was made against (ABA-safe: an
   absent row is row_version 0), writes, re-reads, and refuses unless the re-read state
   hash equals the one the proposal promised. Any refusal rolls back to a savepoint. An
   action proposal has exactly one version (`ActionVersionFixed`): a changed action is a
   new proposal.
5. **One ledger.** The decision row is append-only (SELECT and INSERT only) and holds a
   `ledger_seq` pointer into `dms.ledger_ref`. The Cortex append happens in ACT-02, in the
   same transaction, so a failed append leaves no write. `before_hash` and `after_hash`
   are state digests, not a chain.
6. **The receipt is rebuilt, not trusted.** `record_decision` re-reads the version (its
   status must be `applied` or `rejected` to match the decision), its pins and, for an
   apply, the writeback row that version last wrote, and refuses (`ReceiptMismatch`) a
   caller record that differs in any field. The Space is the proposal's own. One Cortex
   entry id backs one pointer (`LedgerEntryReused`). Evidence, `approval_ref` and the
   reason are secret-scrubbed and PII-masked before the append-only row, as
   `0005_ask_audit` stores a question; an integer in evidence is masked by its decimal
   text. The actor is recorded and must not be blank or the shipped default (DR-0004
   Option A: one configured steward identity, no dual control); an actor that is not a
   user id and reads as PII (an email) is refused.
7. **Who approved is bound at apply, not told at record.** The apply (or decline) writes
   its actor and masked `approval_ref` (and, for a decline, the masked `decline_reason`)
   to the append-only `dms.action_confirmations` row for that version, in the same
   transaction as the status flip. `record_decision` reads them back and refuses
   (`ReceiptMismatch` on `approval_ref` or `actor_id`) a caller record that names another
   ref or actor; the decision row stores the bound values. The receipt's evidence must be
   exactly what was pinned at propose, plus the bound `decline_reason` for a decline: a
   key added at record time (an `audit_id` the proposal never cited, an `approved_by`)
   or a changed one is refused (`ReceiptMismatch` on `evidence`). A proposal cannot pin
   `decline_reason` itself.
8. **A refused record takes its apply with it.** The apply or decline runs under a
   savepoint that stays open until `record_decision` stores the decision. Any refusal of
   the record rolls that savepoint back, so the write, the status flip and the binding are
   gone even if the caller commits afterwards; work the caller did before the apply is
   kept. The version is `pending_confirm` again and can be applied anew.
9. **Object keys never carry PII.** A key value is copied verbatim into the writeback row,
   the summary, the receipt and the Cortex ledger, and masking it would lose which object
   changed. A key value the masker would change (an NRIC, an email) or one longer than 200
   characters is refused at propose (`key_value_invalid`). A reason whose masked form is
   longer than 500 characters is refused at propose, not at every later apply.

## Consequences

- ACT-01 lands the migration (`0009_action_writeback`) and the core only. No route, no
  UI, no Cortex call. Main's behaviour is unchanged with the flag off, and the amend
  confirm path behaves identically on main's data because `dms.action_proposals` is
  empty there.
- ACT-02 adds the route (with `compliance_gate` before side effects, hard rule 8), the
  Cortex ledger append, and the check that the key values match the source encoding
  (hard rule 12). Until ACT-02, an override is stored but nothing reads it into an answer.
- The append-only guarantee holds for the app roles only. The table owner and a
  superuser can still edit, the same caveat as `0005_ask_audit` (a BANK-04 item).

## Confirmation

`tests/test_act_01_spec.py` and `tests/control_plane/test_act_01_writeback.py`, and
`scripts/verify/act_01_writeback_demo.py`, which prints `ACT-01 PROOF OK @<sha>`.
