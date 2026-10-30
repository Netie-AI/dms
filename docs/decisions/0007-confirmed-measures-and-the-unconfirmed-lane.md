---
status: proposed
date: 2026-10-01
decision-makers: founder (decision comment on dms#283 and dms#284), routed via prd-agent; built by the Claude Code lane
outcome: proposed - confirmed measures back certified answers; unconfirmed simple aggregates answer under a distinct non-certified badge; nothing below is built yet
---

# DR-0007 - Confirmed measures, and the unconfirmed lane

This record is `proposed`. It writes down the design the founder decision on dms#283
(ONTO-CONFIRM-01) and dms#284 (CONNECT-ASK-02) implies, the order it ships in, what step 0
verified against the live code and a live Cortex, and what is still unverified. It
changes no product code. It becomes `accepted` when the PRD Agent routes it and the
founder answers the open questions at the end.

## Context and Problem Statement

`dms.onto_measure` has existed since migration 0004 and nothing writes to it. A Space's
ontology is derived and verified (ONTO-DERIVE-01), but the model can still answer a
question about "total budget" with SQL it invented, and the envelope calls that
`L2_VALIDATED`. The founder's decision settles four points:

1. Default path: proposals come from the VERIFIED Space ontology, a steward confirms each
   one, and only confirmed measures can back a certified (L0/L1) answer.
2. Ad hoc lane in parallel: SIMPLE aggregates with no confirmed measure may answer under
   GRAIN-GUARD and the scope checks, with a DISTINCT non-certified badge and an assumption
   line saying the measure is unconfirmed. Never a certified badge. WRONG=0 and named
   abstain still apply. A non-simple aggregate is therefore not answered; it abstains
   `no_declared_measure`.
3. Auto-derived measures presented as certified stay refused.
4. dms#283 is the confirm/reject API plus a manual-entry fallback. dms#284 is the
   generative lane reading the Space's confirmed ontology, with two named abstains,
   `no_declared_measure` and `no_declared_link`.

## Decisions

- **A measure is a structured spec**: an aggregate enum (`count`, `count_distinct`, `sum`,
  `avg`, `min`, `max`) over one landed column (or `*` for `count`) of one grain object. No
  free SQL, expression text, filters, arithmetic or ratios anywhere. The SQL text is built
  by one function in code (`measure_expression`) and rebuilt at load and again inside
  `Ontology.compile`, so a stored row cannot smuggle SQL.
- **States**: the existing three (`proposed`, `confirmed`, `rejected`). No new state.
  Allowed transitions: proposed->confirmed, proposed->rejected, confirmed->rejected
  (withdrawal), rejected->confirmed. A confirmed definition is immutable and carries a
  stored `definition_hash`, enforced by a BEFORE UPDATE trigger and recomputed at load.
- **The proposer is deterministic code in DMS.** Contract 1.2.0 has no Cortex proposal
  call and Cortex is the gate only. Manual entry creates PROPOSED only, never confirmed.
- **Four gate task ids**: `spaces.ontology.measures.propose|create|confirm|reject`. Every
  mutation resolves the measure and grain through the route's own Space (404 otherwise),
  checks the steward role from settings (never a header or body), calls `compliance_gate`
  before any side effect, and appends to the one ledger through Cortex in the same
  transaction as the single UPDATE. A ledger failure fails the confirm closed (503).
- **Badge policy.** A confirmed-measure typed compile ships as `L2_VALIDATED` plus a
  `measure_basis` object behind one constant (`CONFIRMED_MEASURE_BADGE`), and flips to
  `L1_GOVERNED_METRIC` only when every gate in the staged rollout is green. An
  unconfirmed simple aggregate gets a new value, `L2_UNCONFIRMED` ("distinct" is taken
  literally), added only after the UI knows the key. A badge is computed server-side from
  structured provenance, never from Cortex or model prose, and clamped downward in three
  places (builder demotion, `assert_envelope_valid` E14, the egress finalizer).
- **Independent fit (two-key rule).** Cortex ranks measures by name-token overlap, which
  is not authority to certify. DMS runs its own fit check and L1 requires the ranker's pick
  to equal DMS's pick and the question's aggregate cue not to conflict.
- **Rule 12 at ask time.** Plan filter literals are probed against the column as landed;
  a mis-cased `north` abstains `unmatched_filter_value` instead of returning a plausible 0
  under a green badge.
- **Cursor gating.** The Studio measures screen and UI badge parity are Cursor's; the
  backend vocabulary change cannot ship until the UI key exists (`Badge.tsx` destructures
  `BADGE_COPY[kind]` and `AnswerMessage` reads `LAYER_COPY[badge]`, both of which throw or
  break on an unknown badge).

## Staged rollout

1. Backend steps 1-7 (migration `0010_onto_measure_confirm`, core types and store API,
   `measure_spec` validator, steward routes, proposer and confirm criteria, load/prune/fit,
   typed branch) are inert for asks and can merge freely.
2. Cursor lands UI parity: `BadgeKind`, `BADGE_COPY` and `LAYER_COPY` with
   `L2_UNCONFIRMED`, and `Badge.tsx` degrading any unknown badge to a neutral "not
   certified" chip instead of throwing.
3. Only then: step 8 (vocabulary, E14, parity test) and step 9 (emission of
   `L2_UNCONFIRMED`, the ad hoc classifier on the ask path, the egress finalizer).
4. Step 11 flips `CONFIRMED_MEASURE_BADGE` to `L1_GOVERNED_METRIC` only when ALL are
   green: E01-E06, E11-E14, E19; the contract-ask egress tests; UI parity merged; the gate
   check below recorded. If any drift, tamper, precedence or fit test cannot pass, do not
   flip: the lane stays `L2_VALIDATED` plus `measure_basis`. That is the safe fallback.
5. The Studio measures screen is Cursor's, built from the route table posted on the issues.

Carve-out in force for this build: steps 8, 9, 9b and 11 are founder/Cursor gated and are
NOT built. No new badge value appears in product code, `CONFIRMED_MEASURE_BADGE` stays
`L2_VALIDATED`, and ad hoc generated SQL keeps today's `L2_VALIDATED` path. See P-DMS-48.

## Step 0 verification (2026-10-01, branch `claude/onto-derive-01`, HEAD `20211d6`)

**Tree.** `git status` clean; HEAD `20211d6` is the merge commit (parents `50c34ad`,
`14d6765`).

**Symbols.** Every symbol the spec cites that is meant to already exist was re-grepped and
found; none moved by more than a few lines (for example the `kind == "miss" and
ranked_slots is not None` block is at `generative_ask.py:1321`, the spec says ~1326, and
`budget_space_block` is at `space_ontology.py:553`). Symbols that do not exist yet and are
the new work: `classify_adhoc_shape`, `_require_steward`, `measure_spec`, `measure_use`,
`measure_confirm`, `measure_basis`, `MeasureProvenance`, `finalize_space_envelope`,
`REASON_NO_DECLARED_LINK`, `relabel_join_gap`, and the new test files. `alembic/versions`
ends at `0006_onto_snapshot` (PR #316, chained after `0005_ask_audit` of PR #342), so `0007_onto_measure_confirm` was free when this was written. At merge main had taken
0007..0009, so the migration is `0010_onto_measure_confirm` on `0009_action_writeback`, and
this record, first numbered DR-0006, is DR-0007 (DR-0006 is the writer family, #440). The DR
number and the migration number are independent counters.

**Baseline** (`DMS_SKIP_CONTROL_PLANE_TESTS=1`): `tests/test_onto_derive_01.py`,
`test_ingest_fixes_277.py`, `test_fanout_guard.py`, `test_sql_source_typed_ingest.py`,
`test_onto_store_01.py`, `test_gen_path_refuse.py`, `test_ontology.py`,
`test_space_gen_01_round3.py` plus `tests/invariants`: **339 passed, 0 skipped, 0 failed**
in 108 s. `tests/control_plane`: **32 passed** against Postgres 16 (the server had to be
started and the `dms` role and database created in this sandbox first; the suite fails
hard, by design, when Postgres is absent). Static checks: `ruff check` clean, `mypy`
no issues in 152 source files, `lint-imports` 3 contracts kept.

**Live Cortex gate probe.** A local Cortex from `/home/user/netie-ai/cortex` (pack `dms`,
model stubbed, real uvicorn) was started and called through DMS's own
`cortex_client.compliance_gate`, then stopped (no process left). Result:

| task id | allowed | reason |
|---|---|---|
| `spaces.ontology.derive` (known good) | True | `pass` |
| `amend.confirm` (known good) | True | `pass` |
| `spaces.ontology.measures.propose` | True | `pass` |
| `spaces.ontology.measures.create` | True | `pass` |
| `spaces.ontology.measures.confirm` | True | `pass` |
| `spaces.ontology.measures.reject` | True | `pass` |
| `spaces.ontology.measures.nonexistent_control` (control, exists nowhere) | True | `pass` |

Raw `POST /dms/tasks/gate/check` for `...measures.confirm`: HTTP 200
`{"status":"pass","violations":[],"executable":true,...}`. No id answered `unknown` or
`denied`. Reading `packs/dms/tasks/gate.py` explains why: `check_task` evaluates only the
`filled_template` and never consults a task catalog, and the route's 404 fires only for an
unknown event id on `/gate/acknowledge`. **Conclusion: today there is no catalog to
request entries from, the four ids need no Cortex change, and the gate is task-id-blind**
(it would also pass an id nobody ever defined). Two consequences. First, a green gate for
these ids is not evidence the gate "knows" them, so the gate is not what protects the
measures workflow; the steward-role check, the single write path and the ledger entry
are. Second, the exposure remains if a future Cortex adds a real catalog: every
uncatalogued DMS mutation id (these four, `spaces.ontology.derive`, `amend.confirm`) would
403 in production while CI stays green, because tests monkeypatch
`routes.spaces.compliance_gate`. Re-run this probe when Cortex changes (P-DMS-4).
`gatekeeping.enforce(..., mutation=True)` is fail-closed for `gate_unavailable` and
`gate_task_unknown`; PARKING_LOT P-DMS-4 said "soft-allow" and has been corrected.

**Actor row.** The configured actor defaults to `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb`
(`settings.dms_actor_user_id`). Only `seed_demo_tenant` (called from the app lifespan when
`DATABASE_URL` is set, and a no-op once the demo tenant exists) inserts that `dms.users`
row. By reading: the default actor is provisioned on a seeded database; a deployment that
sets a different `DMS_ACTOR_USER_ID` has no row and every confirm would hit the
`decided_by` foreign key. Hence the planned `503 actor_not_provisioned`. Not exercised
against a non-seed database in this step.

## Consequences

- Certified answers need a steward-confirmed measure, a hash that recomputes, a column that
  still exists with the same type, and an independent fit. Anything less degrades to a
  labelled or abstained answer, never upgrades.
- Cost: one measures query, one DESCRIBE per distinct grain relation and up to one probe
  per plan filter literal per ask.
- Questions needing arithmetic, ratios or measure-level filters abstain on a Space until a
  pilot needs them (P-DMS-37).

## Ask-time lane as built (step 10)

- **Precedence is decided once, before the extract loop.** A Space with a usable confirmed
  measure ranks first; when Cortex ranked a measure AND DMS's own `measure_fit` lands on the
  same one, the ranked plan replaces valid generated SQL. `DMS_CLOOP_B` could route around
  this (the loop runs model SQL only and returns before the typed branch), so the same
  decision now runs ahead of the loop and a fitting measure is never the loop's to answer.
  Fit fails with SQL present: the SQL keeps today's generated path (it becomes `L2_UNCONFIRMED`
  at step 9). Fit fails with a plan only: `no_declared_measure ... fit_disagrees:<why>`.
- **A breakdown the plan computes is covered.** `by` / `per` / `each` followed by a word the
  plan groups by (`grouped_by`: its object and column names) is not a qualifier veto;
  `excluding`, `except`, `without`, `only`, `not`, `other than` never are. Without this the
  hardened qualifier rule would refuse every grouped ask on the confirmed lane.
- **Rule 12 at ask time.** Every `=` / `IN` literal of a plan's filters is probed against its
  column as landed before compile (`unmatched_filter_value`; unreadable or over-long lists
  fail closed). An aggregate that is NULL in any returned row, or a plan with no rows,
  is `empty_aggregate`. `COUNT` over an empty table is 0 and answers: 0 is a value.
- **After a confirmed plan abstains the ask never falls back to Cortex SQL.** Two paths would
  be two numbers.
- **The question and the plan are accounted for exactly** (verifier findings across three
  rounds, fixed at the root). The first cut checked aggregate cues and a list of qualifier
  words; the second a word allowlist the tokenizer could not see past (non-ASCII); the third
  Unicode tokens compared as lower-cased stems, where a frame word that is also a legitimate
  VALUE still counted as covered ("total budget of schools in the US" answered the unfiltered
  total, as did "OR", "IT", "IN", "A", "Wills", "Doe", "Cans"). A word or stem list reaches a
  ceiling after a few rounds, so `measure_use.account_for_question` replaces it with exact
  accounting and default deny, on the RAW NFKC-normalised question with its case:
  1. Every plan filter literal (scalar or `IN` element, as the plan carries it: sign, decimal
     point, date punctuation) must occur in the question as an exact, case-sensitive
     substring on token boundaries, and its span is removed. A literal the question does not
     state, an operator other than `=` / `IN`, an empty, NULL, boolean or nested literal, a
     `keep_gt` or an `offset` refuses as `unstated_filter` (`-2` is not `2`, `1.5` is not
     `5.1`, `2024-01-05` is not `2024-05-01`, `5` is not stated by the `5` of `2.5`).
  2. Everything left is accounted for. A character that is not a letter, digit or mark,
     whitespace, `? . ,` or a final `!` refuses and names its class (symbol, punctuation,
     control or format character): emoji, backticks, `$`, `&`, `*`, `-`, apostrophes.
  3. Each remaining token is exactly one of: an aggregate CUE `measure_fit` already
     validated (whole phrases such as "number of"); a FRAME word from a closed lowercase list
     of 35 words matched case-sensitively with no stemming and no plural folding (pronouns
     and connectives such as `me us it or to be as by per each every across where this that
     has have will` are NOT frame words); a leading request phrase ("show me", "tell me",
     "give me", "can you", "could you") stripped positionally; or a word of the measure's
     name, its grain table, or a name the plan groups or filters by, compared exactly and
     lowercase with one plural rule (`-s`, `-y`/`-ies`, either direction) that is never
     applied to a token that was capitalised. Only the first token may be capitalised
     (`Title`, lower-cased for the test). Every other token, including any with an uppercase
     letter, a digit or a non-ASCII letter, is a condition the plan dropped
     (`unbound_constraint`). `by` / `per` / `each` / `every` are covered only when the
     plan's group-by names the next word (a column, or a table other than the measure's own).
  With SQL present the SQL stays on today's path (`confirmed_measure_not_preferred:
  unbound_constraint`); with only a plan the ask abstains `no_declared_measure`.
- **Guards found by the confirmed-lane adversary round** (all pinned at the envelope in
  `tests/test_confirmed_lane_guards.py`; each was disabled in turn and its tests went red):
  1. *A relation's landed name is not vocabulary.* `bronze.public_schools` is table
     `schools` of source schema `public`, so the table words in item 3 are the table's name
     AT THE SOURCE (`measure_use.read_source_pulls`, from the ingest registry), never the
     landed relation name: "total budget of public schools" is a condition the plan does not
     carry, whatever the schema is called (`public`, `dbo`, `hr`, `archive`). The same holds
     for the filter table and the group-by table. A relation the registry cannot name
     contributes no word at all (default deny).
  2. *Two conditions on one column are never what a question states.* "North and South" is
     one `IN` list; as two equalities the plan ANDs to the empty set and `COUNT` prints 0.
     Two filters on one column (however spelled), or an `=` beside an `IN`, refuse
     `unstated_filter`. A literal that `k` filters use must occur `k` times in the question
     (one `North` cannot state both a district and a region); an `IN` list names a value once.
  3. *A source cut after the confirm is not the source that was confirmed.* `prune` reads
     the latest snapshot's `truncated` flag AND the ingest registry as it is now, and drops
     the measure as `source_truncated`; an ingest record that cannot be read or was never
     written drops it as `lake_unreadable`. Only the grain's own completeness is a
     measure-level fact: a cut parent leaves orphan keys that `verify()` already refuses.
  4. *The plan's `limit` is a row cap the question never stated.* A grouped confirmed plan is
     compiled with `CONFIRMED_ROW_CAP + 1` (200 + 1) rows whatever its own `limit` says, and
     the answer abstains `too_many_groups` when that extra row exists; an answer under the
     cap is whole and its coverage no longer lists a LIMIT exclusion. An ungrouped plan is
     one row and keeps the SQL the steward saw at confirm.
  5. *A route among several is nobody's pick the question made.* The compiler refuses
     `ambiguous_path` between parallel links without a `via`; with one it would answer, so
     `via_choice_reason` refuses the same plan. That closed the parallel-link repro and not the
     class: with `enrollments.school_id -> schools` and `schools.district_id -> districts`
     beside a direct `enrollments.district_id -> districts`, "enrollments in North" is the
     enrollment's own district (1 row) or its school's (3 rows), a `via` naming the only link of
     its hop passed, and with no `via` the compiler took the shorter route in silence. Now
     `route_choice_reason` counts the routes from the measure's grain to EVERY object the plan
     groups by, filters on or names in its `via` (`Ontology.routes_between`, the routes without
     the `via`, many-to-one only for a grouping and every verified link for a filter, parallel
     links counting separately) and refuses `ambiguous_path` when there is more than one, with
     or without a `via`. The count stops at two and spends at most 20,000 link inspections; a
     graph it cannot finish counting is refused too. A `via` that names the grain itself (a
     self-link, the parent's reading beside the row's own) is refused. Deviation from the
     smallest fix: the no-`via` default is closed in this lane as well, because a `via` equal to
     the default would otherwise be refused while the same plan without it answered. Every other
     lane still takes `Ontology.compile`'s shortest path, unchanged.
  6. *Cheap P2s from the same round.* A text literal whose column also holds another
     spelling differing only in case or surrounding spaces (`North` and `north`) refuses
     `ambiguous_filter_value`; the equality would match one spelling and drop the rest (one
     probe query per literal, as before). A COUNT measure answers only a question that asks
     for a count (`how many`, `number of`, `count`, `tally`): "show me the schools" asks for
     rows.
  7. *"North and South" over two different columns is a list the plan ANDs.* A district filter
     plus a region filter, the question "How many schools are there in North and South?": the
     plan matches no school (COUNT printed 0) or the few that are both (a believable wrong
     figure). `_joined_values` refuses `unstated_filter` when two neighbouring stated values
     sit in different filters (a value used by several filters is not provably in any one) and
     what lies between them is only a join: an `and` or a comma, with frame words and
     separators. One filter's own `IN` list is the one case where that join is right. Two
     values side by side ("March 2024", "Q1 2024") are two conditions and still answer, and so
     does a phrase of its own between them ("public schools in North", "schools are public in
     North", "In North, how many schools are public?": `schools`, `public`, a verb or a
     preposition with no `and`). The price is a false refusal of "schools that are public and
     in North": `and in` joins two conditions as readily as two places, and the text cannot
     tell which.
- **A NULL aggregate is not a figure.** A confirmed answer whose measure is NULL in ANY row,
  or that has no rows, abstains `empty_aggregate`: dropping the group would hide one the
  customer asked about and printing it would read `=None` under the badge. A NULL group KEY
  is a real group and reads `(null)`.
- **A Python bool is not 1.** The value probe requires a BOOLEAN column for a boolean literal
  (`TRUE = 1` holds in SQL).
- **The `measure confirmed:` sentence is exempt from the PII scan** the way the basis it
  restates is. A digest prefix or ledger id shaped like a phone number is an identifier DMS
  minted; masking it makes the digest uncomparable. The spelling and the identifier shapes
  live once, in `dms_core.measure_line`, and the scan exempts a string only when it equals
  the sentence a shape-valid basis spells, under `assumptions` only.
- `measure_basis` is additive on the envelope, built with its sentence by one helper
  (`confirmed_basis`), kept only when it is a valid confirmed basis for the badge and the
  assumptions, else the answer clamps to ABSTAIN. The key is exempt from the PII scan
  (`SAFE_ENVELOPE_KEYS`) because every field is pinned to an identifier shape.

## Residual risk and limits (recorded so they are not re-discovered)

- **Stale by meaning is undetectable.** A column that keeps its name and type but changes
  meaning keeps answering until a steward withdraws the measure. The per-ask checks cover
  existence, type, grain verification and column presence, not semantics.
- **"Steward confirmed" is a configuration plus a network position, not a person**
  (DR-0004 Option A). The wording says "steward account". Four-eyes is impossible today.
- A shape-valid, fan-out-safe measure can still be the wrong business definition (SUM of a
  non-additive column). Mitigations: required description, the executed probe shown
  before confirm, the fit veto, the ledger trail. Structure cannot prove meaning.
- The in-memory ontology store loses confirmations on restart; certified answers silently
  revert to the labelled lane. `persisted: false` and a permanent Studio banner are the
  mitigation (rule 11's lesson).
- Cortex limits are copied, not shared (rule 1): the SQL-ish description pattern, the
  identifier regex and the size caps. Drift would 4xx every ask on a Space, which is a
  named abstain, not a wrong number. DMS caps are stricter.
- A ledger outage fails confirm closed. An orphan ledger entry after a failed commit
  confers no authority: the ask path trusts only database rows plus the hash recompute.
- The confirmed lane does not answer ranking or range phrasing ("top 3", "between",
  "at least", "over 100"), a contraction, a pronoun, a quotation or a normalised value ("KL"
  for "Kuala Lumpur"): no rule above admits them, so a plan that carries them is refused. A
  false refusal is safe, and with SQL present the generated path answers.
- A parent-table grouping is demoted by the envelope's own E9-02 scope check when the joined
  tables share a prefix (`public_schools`, `public_districts`): a false refusal in the
  envelope, not in this lane.
- A conjunction across two columns is refused only when frame words alone join the values
  (guard 7). A value restated with a phrase of its own ("schools in North and schools in
  South", "North and the number of schools in South") is read as two statements, and the plan
  still ANDs them: a believable 0 or a small wrong figure. The text cannot tell a second
  subject from a second condition. Unlock: a plan-side signal (Cortex reporting an `OR`/list
  intent) or a phrase-to-column map.
- A dimension reachable by more than one route is refused (guard 5) however the question is
  worded, and a `via` is never accepted as the customer's pick, even when the question says
  "mail district": false refusals until a proven phrase-to-route mapping exists. A table that
  holds a redundant foreign key beside a chain (an order line with `customer_id` and
  `order_id -> customer_id`) loses the certified lane for that dimension.
- Accounting is by token and literal, not by relation. A literal is stated when the question
  contains it exactly; the gate cannot tell whether it is stated about the column the plan
  filters ("district 2" and "2 districts" both state `2`). The value probe bounds it
  (`unmatched_filter_value`) and P-DMS-53 stays open. A dangling frame phrase ("...in a") is
  accepted: it names no value. A vocabulary word that is also a value (a table called `us`)
  is accepted, because it is a name the plan itself carries.
- Coverage ceiling of the certified lane: Cortex ranks by name tokens and slot filling is
  demo-shaped, so early L1 hits will be mostly ungrouped totals and simple dimensions;
  English tokens only. Measure and report certified hit rate per Space; never claim coverage.
- Standing P0 candidate outside this slice: EXPLAIN on a write-mode DuckDB connection
  (P-DMS-47).
- **Until the egress finalizer ships (P-DMS-48), an `L1_GOVERNED_METRIC` or `L0_CERTIFIED` on a
  SQL-source Space is NOT proof of a steward-confirmed measure.** The contract-ask tail trusts the
  engine's badge, so a `governed_metric` or `certified` answer over single-table SQL keeps it with
  no `measure_basis` (P-DMS-57, pinned by `tests/test_space_contract_ask_badge_gap.py`). A
  confirmed-measure answer is recognised by `measure_basis` and the `measure confirmed:` line, not
  by the badge. Pre-existing on the #316 baseline; this lane did not introduce it.

## UNVERIFIED (spec section 12), with the check that settles each

- **Live gate catalog.** Probed above against a local Cortex: passes for every id. Still
  unverified against the deployed Cortex image; settles with the probe re-run on the
  deployed tag.
- **`dms.users` row for the configured actor on non-seed deployments.** Read from code,
  not exercised. Settles with the PG test for `actor_not_provisioned` and a deploy check.
- **Cortex returns an ontology ranking alongside `query_sql` for `source=space`.** The
  router depends on it; without it the confirmed lane is reached only via kind `miss` or a
  typed plan. Settles in the router-precedence test (E05) against the real harness.
- **`build_audit_receipt`'s signature** (`envelope.py:1216`). Read it before writing the
  restamp helper (step 9, carved out).
- **PG DDL** (trigger, row-value `IN`, named CHECKs) has not been run. The PG tests are
  the check.
- **How many existing tests flip, and whether the demo pack ask can fire on a Space
  (E19).** Settles after emission lands; a hit on a non-demo Space becomes its own P0.

## Open founder questions

The spec refers to four founder questions (the proposer being deterministic code is
question 2, English-only fit tokens is question 3, ontology version promotion is question
4). The spec file as delivered to this step does not contain their text, so they are
not restated here; the PRD Agent should take them from the decision comment on dms#283
and dms#284.

## Related

DR-0004 (identity is configuration), CLAUDE.md rules 1-12, PARKING_LOT P-DMS-4 and
P-DMS-37 to P-DMS-48, `docs/decisions/0001-record-decisions-in-this-repo.md`.
