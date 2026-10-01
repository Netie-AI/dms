---
status: proposed
date: 2026-10-01
decision-makers: founder (decision comment on dms#283 and dms#284), routed via prd-agent; built by the Claude Code lane
outcome: proposed - confirmed measures back certified answers; unconfirmed simple aggregates answer under a distinct non-certified badge; nothing below is built yet
---

# DR-0006 - Confirmed measures, and the unconfirmed lane

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

1. Backend steps 1-7 (migration `0006_onto_measure_confirm`, core types and store API,
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
ends at `0005_onto_snapshot`, so `0006_onto_measure_confirm` is free. Note that this
DR's number (0006) and that migration's number are independent counters.

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
- Coverage ceiling of the certified lane: Cortex ranks by name tokens and slot filling is
  demo-shaped, so early L1 hits will be mostly ungrouped totals and simple dimensions;
  English tokens only. Measure and report certified hit rate per Space; never claim coverage.
- Standing P0 candidate outside this slice: EXPLAIN on a write-mode DuckDB connection
  (P-DMS-47).

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
