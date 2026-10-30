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
   rows it checks `sql_hash`, `result_hash` and `order_hash` against them.
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
   cannot be resolved is all-PII), `result_out_of_order`, `ungranted` (zero submits; the
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
