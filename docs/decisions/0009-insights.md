---
status: proposed
date: 2026-10-29
decision-makers: founder (stamp pending). Drafted by the Claude Code INSIGHTS-PROOF lane, ticket INS-PROOF-01.
outcome: pending founder stamp. Nothing here is accepted until the founder changes the status.
---

# DR-0009 - Business insights carry their proof, or they are refused

Numbering: DR-0007 is held back for whichever of #440 / #461 merges second (both claim
DR-0006). DR-0008 is reserved for the ACTIONS lane. This record is DR-0009. Checked at
origin/main `58fcaa5` and across every remote branch on 2026-10-29.

## Founder amendment text

The founder's request that opened this lane, as relayed on 2026-10-29:

> While keeping this run turned on scale and build all sessions together towards
> perfectness work with all different session uc may escalate call more sseions and work
> towards the best ai dbgpt paantir driven ai, with actions and a best solutiosn and
> business isnights with proof

The dispatch also restated the intent as: build "the best AI, DB-GPT / Palantir driven,
with actions, best solutions and business insights with proof". The amendment-text field
of the dispatch was empty. The founder may replace this section with the exact wording
they want in the PRD. This record does not amend the PRD by itself. It has to go through
the routing rule in CLAUDE.md: the prd-agent decides which epic it belongs to.

## Context and Problem Statement

"Retrieve insights" is where data products usually hand control to a model, and the
model's paragraph contains numbers nobody can point at. That is the F26 shape that E9
refuses. `scripts/insights.py` already mines insights with no model involved, but it
runs outside the served path. Its headlines print Python-computed shares, and it keeps
no record that a reader could check later.

"With proof" needs a definition that a test can check. Without one, every insight
surface will claim it.

## Decision Outcome (proposed)

An insight is served only when all of the following hold. Otherwise it is refused with a
closed code and a fixed customer sentence:

1. **Cells, not arithmetic.** Every figure in the headline is a cell of one executed
   statement: the windowed wrapper around `Ontology.compile`. The gate is strict. Label
   strings are removed first, then every numeric token, bare integers included, must
   equal a row cell or one of the integers `k` and `n` that the template states.
2. **Conservation.** The grouped total equals a raw total computed with no join, within
   `0.005 * groups + 0.005`. That bound is the most rounding the 2 dp measure can explain.
   The raw total must be positive, and so must every part: the measure is computed once
   per fact key (`GROUP BY` the fact's declared key) and no part may be below zero. A
   return of -1000 netted inside a positive territory is `signed_measure`, not a share.
3. **Run twice, in order.** The wrapper goes through the injected `submit` twice. Both
   runs must return the same `item_result_hash` (row set), the same `order_hash` (rows in
   sequence), and `sql_vote.rows_equal` must agree. The templates read rows by position,
   so both runs must also be in the order the SQL asked for: value non-increasing, the
   NULL label last, `cum_share_pct` non-decreasing and within rounding of the running
   `share_pct` sum, each `share_pct` within rounding of its value over the total.
   Otherwise `result_out_of_order`.
4. **No refresh mid-proof.** Ingest watermarks are read before the raw total and after
   the second run, and must match. Registry names are matched case-insensitively. An unknown watermark is recorded as null and is never
   claimed fresh. When any watermark is unknown (the demo lake has no bronze registry),
   the envelope says so in `assumptions`: the refresh check did not run.
5. **The proof is a sibling of the envelope.** It is never a key in it. Its grammar is
   closed (`assert_proof_shape`): hashes, UTC timestamps, identifiers, closed enums,
   finite numbers, and definition text copied verbatim from the ontology. It is also
   self-consistent (re-execution not dated before the first execution, conserves within tolerance, one result hash across both runs and both
   executions, row counts equal `n_groups`, no negative part), and given the SQL and the
   rows it checks `sql_hash`, `result_hash` and `order_hash` against them. Given the
   insight it is attached to, its `insight_id` and `kind` must name that insight: two
   kinds read from one statement share every other field, so a sibling's proof is
   otherwise self-consistent.
6. **Badge by provenance.** A steward-approved item (`Ontology.from_store`) is
   `L1_GOVERNED_METRIC`. The built-in demo ontology is `L2_VALIDATED` and says so in
   `assumptions`.
7. **Refusals.** `non_additive`, `volatile_measure`, `signed_measure`, `empty_fact`,
   `pii_dimension` (never executed; a pii item matches every table an object's relation
   reads, resolved by the FROM/JOIN regex and sqlglot's scope walk united, so subqueries,
   comma joins and spaced dots are included; aliases of a marked column carry the mark;
   the subquery walk is an allowlist: every projection must be a column or an alias over
   columns, literals and a fixed list of pass-through operators and functions, and no
   column may name a table or alias. Anything else (a positional `#n`, `COLUMNS()`, a
   bracket or `struct_extract`, a whole-row reference, a window, a scalar subquery, a
   table or CTE alias column list, a star, a set operation, PIVOT, VALUES, UNNEST,
   LATERAL, an unaliased computed projection) is all-PII, and an object whose tables
   cannot be resolved is all-PII; a view is read through its own SELECT from
   `duckdb_views()`, nested views included, so a view that renames a marked column
   carries the mark, and an unreadable view or catalog is all-PII; a generated column
   (`information_schema.columns.generation_expression`) whose expression reads a column
   marked on its table, or holds anything outside the same allowlist, carries the mark,
   and a catalog that cannot list generated columns is all-PII; a column that shares
   a JOIN, WHERE, HAVING or QUALIFY conjunct with a marked column carries the mark; the
   name-only pii form `Schema.Table.column` splits at its last dot),
   `result_out_of_order`, `ungranted` (zero submits; the
   same united table set is checked before any submit), `measure_revoked`, `not_conserved`,
   `result_changed_between_runs`, `source_refreshed_during_proof`, `headline_unbacked`,
   `too_few_groups`, `too_many_groups`, `cortex_unavailable`, `cortex_submit_failed`,
   `envelope_invalid`, `insights_budget`, `ontology_unverified`, `no_approved_measure`.
   Every one is built by `abstain.build_abstain` at stage `insight_proof`.

### What this does not prove (scope)

- `result_hash` covers the rows before PII masking. A masked envelope is refused
  (`pii_dimension`), so the hash always describes the rows that are shown.
- Contract 1.2.0 cannot read a ledger entry back, so `proof.ledger` is null. Anchoring
  the proof hash in the one ledger (`/v1/contract/ledger/append`, `ledger_ref` pointers
  only) is a later ticket. It needs a contract read-back or an explicit decision that an
  append receipt is enough.
- Two runs that agree show the result is stable. They do not show the result is
  correct. Correctness rests on the verified ontology and on the conservation check.

## Consequences

- INS-PROOF-01 ships the executor core behind `DMS_INSIGHT_PROOF`. The flag defaults
  off and is not exposed on a route. Nothing on main imports the module. The 2026-10-30
  pilot deploy of `58fcaa5` is unaffected.
- A later ticket can put this behind a route (a mutation-free GET that still runs
  `compliance_gate` if it records anything) and let a model *phrase* proven insights.
  The model must never *produce* the figures. The gate in (1) is what keeps a model's
  rephrasing honest.

## Confirmation

`tests/test_insight_proof_01.py` asserts the rendered text and returned rows of proven
envelopes on the demo lake and on an approved store, the proof hashes against a
re-fetch on the test's own connection, and every refusal code above that the fixtures
can reach. `scripts/demo_insight_proof.py` prints the proof. Figures are local claims at
the commit the PR names.

## INS-PROOF-03 addendum (proposed, same stamp)

A steward may pin a proven insight. This is the action-type equivalent of "a human
confirmed it": nothing is auto-pinned, auto-replayed or auto-applied.

- Preview re-proves the one candidate server side and mints a process-local, single-use
  token (600 s). Confirm spends it, refuses a demo-ontology insight
  (`measure_not_approved`), re-proves and refuses a changed result (`stale_preview`), then
  appends one `insight.pinned` pointer (hashes and ids, no SQL, rows or labels) and only
  then writes `main._proven_insights`, a DMS-owned DuckDB table on the
  `main._verified_queries` precedent. No migration.
- `approved_by` is the single configured deployment actor (DR-0004 Option A). There is no
  dual-control claim. Who may confirm is F5's decision through `compliance_gate`.
- Replay checks the approved definition first (no submit when it moved), then runs the
  pinned SQL twice: unchanged is `L0_CERTIFIED`; changed is `L1_GOVERNED_METRIC` with a
  label-level drift block and no computed delta. Every verdict appends `insight.replayed`.
- Ceilings: tokens do not survive a restart (`confirm_token_unknown`); drift says
  `source_change_unknown` when a watermark is unknown; contract 1.2.0 still cannot read
  an entry back.
- Hand-off to ACTIONS (DR-0008): `asset_id` plus `expected_result_hash` is the citation a
  future writeback proposal must carry.
- Verify round 1. Replay re-checks governance, not only the measure, before any submit:
  every ontology item the pinned SQL was compiled from (measure, objects, links) must
  still be served at its approved version, the SQL must recompile byte-identically, and
  the INS-PROOF-01 PII gate runs on the pinned dimension. A miss is
  `definition_changed`. GET /pins withholds a stored headline whose dimension is now PII,
  and revoke clears the stored headline and rows. A replay re-reads the pin under the
  revoke lock before its append. A re-prove that fails on Cortex is a 503
  (`cortex_unavailable` / `cortex_submit_failed`) and gives the token back. A served
  replay whose audit row is dropped is demoted to ABSTAIN `audit_unrecorded` and names its
  orphan entry; confirm reports `audit_recorded`. The replay receipt names the deployment
  actor. One active pin per (space, SQL) is also a UNIQUE index on `active_key`.
- Verify round 2.
  - Every pin route (preview, confirm, replay, revoke, the list) calls `compliance_gate`
    with `actor` = the configured deployment actor and `filled_template.space_id` = the
    Space (from the request, the token or the pin), exactly as `/run` does. F5 is told who
    is confirming; it is still F5's decision.
  - Confirm refuses the shipped default actor (`bbbbbbbb-...`) as `steward_identity_unset`
    (403) before the gate, the token or any append: with no steward configured, no pin may
    say a steward approved it (ACT-02 refuses the same case). One configured steward, no
    dual control.
  - Receipts are deduplicated across the pin lifecycle. `main._proven_insight_receipts`
    holds one row per receipt a pin, replay or revoke accepted (entry id primary key,
    seq unique); the register's own pointers are checked too. A receipt seen before is
    `ledger_receipt_invalid:repeated` (503) and nothing is written or served. The register
    and this receipts table are the only tables INS-PROOF-03 writes; a replay now writes
    one receipts row and no register row.
  - A register write that raised is re-read before the answer. A pin row (or revoke) that
    landed is reported as landed (200); only a row that is truly absent is
    `pin_commit_failed` / `revoke_commit_failed` with its orphan entry; an unreadable
    register is `pin_commit_unknown` / `revoke_commit_unknown`. A replay whose receipt row
    cannot be confirmed is `ledger_receipt_unrecorded` (503) and is not served.
  - A Cortex failure during replay is not a verdict: it is `cortex_unavailable` /
    `cortex_submit_failed` (503) with no `insight.replayed` append, as at preview and
    confirm. "Every verdict appends `insight.replayed`" holds for data verdicts only.
  - Ceiling: receipts from `/run` (`insight.proven`) are deduplicated within one run only;
    they are not persisted, so a pin route cannot compare against them.
