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

## ACT-02 addendum (proposed, same stamp)

ACT-02 mounts the routes under `/v1/amend/actions` (a sub-router of the amend router, so
`app.py` is untouched). They match nothing unless `DMS_ACTIONS=1` and are never in the
OpenAPI schema, so with the flag off every method and body there gets main's 404.

1. **This deviates from Wave 3.** PRD Wave 3 has EPIC-005b apply through Cortex
   `call_action` (EPIC-005a). Here DMS applies in-process, to a DMS-owned table, and
   Cortex only receives the ledger receipt. That is the amendment the PRD Agent routed to
   the founder (re-ask of F53 / F54(a)); it is not settled by this PR. An engine-side
   action still needs EPIC-005a.
2. **The registry is closed, and its path to Cortex is a pack entry.** One type, in code.
   When the Cortex ontology pack declares governed actions, the type moves there and DMS
   reads it; until then a new type is a reviewed DMS change, never a request.
3. **Same identity.** Under DR-0004 Option A the configured actor
   (`settings.dms_actor_user_id`) both proposes and applies. Every receipt says
   `identity: config_actor (DR-0004 Option A)` and
   `separation_of_duties: {proven: false}`. The shipped default actor is refused
   (`steward_identity_unset`), and the steward grant is checked
   (`steward_grant_required`). No dual-control claim is made anywhere.
4. **Commit-after-append window.** The ledger append runs inside the transaction, before
   the commit, so a failed append writes nothing (503 `ledger_append_failed`). If the
   commit itself fails after a successful append, the entry exists and the write does
   not. A commit can also land and still raise (the connection drops after COMMIT is
   sent), so the route first reads back, on a fresh connection, whether the decision row
   naming the entry exists. If it does, the write committed and the receipt is returned
   (no void). If it does not, the route makes a best-effort `action.apply.void` (or
   `action.decline.void`) append naming the entry and answers 503
   `commit_failed_after_append` with the entry id. If the database cannot answer, it
   appends nothing and answers 503 `commit_outcome_unknown` with the entry id, because a
   void and silence could each be wrong. An append that returns a blank entry id is
   refused (503 `ledger_append_failed`, `reason: empty_entry_id`) and rolled back.
   Remaining, disclosed: an append that Cortex accepted but whose response was lost (a
   timeout) looks here like a failed append, so it rolls back with no void, and the
   steward's retry appends a second `action.apply` for the same `version_id`. The first
   is then an orphan entry that no `dms.ledger_ref` points at; closing that needs an
   idempotency key on the Cortex append (a Cortex contract change). This window is
   disclosed, not closed.
5. **F5 holds no `action.*` policy yet.** The routes call `compliance_gate` with task ids
   `action.list`, `action.propose`, `action.apply`, `action.decline` and `action.revert`
   and fail closed on a refusal (mutations treat `gate_unavailable` and
   `gate_task_unknown` as refusals). Whether the deployed Cortex catalog knows these ids
   was not verified; a live stack may answer 403 until the Cortex lane packs them. The
   Cortex ledger must also accept event types `action.apply`, `action.decline`,
   `action.apply.void` and `action.decline.void`. DMS adds no bypass for either.
6. **Rule 12 and E9 at propose.** The object key must match exactly one source row as
   encoded (`BETA` is refused with the candidate `SKU-BETA`). A cited answer
   (`evidence_audit_id`, the envelope `audit_id` = `ask_audit.cortex_entry_id`) must be
   validated, have executed SQL, return rows and have read the source table; apply
   re-checks it. An answer asked in a Space backs actions in that Space only
   (`evidence_wrong_space`); one asked with no Space is tenant-wide. The table check
   compares the last name segment, so `<schema>.inventory` from any source counts as
   having read `inventory`. That is loose, and is tightened when a second source kind
   lands. A key that misses gets up to five candidates matching any key field, best
   match first.
7. **Revert is a new proposal.** It is made against the reverted decision's after-state,
   refuses if the cell has moved on, and still needs its own apply.
8. **Free text is masked, not kept out.** `approval_ref` (at most 200 characters) and the
   reasons (at most 500; a longer decline reason is refused, never truncated) reach the
   Cortex payload and the append-only rows secret-scrubbed and PII-masked, the way
   `ask_audit` stores a question. Whether free text should instead stay DMS-side only,
   or be hashed, is open for the PRD Agent. A configured actor that is not a UUID is
   refused (`steward_identity_invalid`). The decline route passes the reason to the core
   decline, which binds it with the actor and `approval_ref` (item 7 above); the ledger
   payload's `reason` and the receipt's `decline_reason` are that bound value. A key value
   that reads as PII is refused at propose with 400 `key_value_invalid` (item 9 above).

Confirmation for ACT-02: `tests/test_act_02_flag_off.py`,
`tests/control_plane/test_act_02_routes.py`, `scripts/verify/act_flag_off_parity.py` and
`scripts/verify/act_02_route_demo.py`, which prints `ACT-02 PROOF OK @<sha>`.
