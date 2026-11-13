# CONNECT-ASK-03

PREFLIGHT: PARTIAL. Findings cover ONTO-DERIVE-01, DEF-VALIDATE and RECONFIRM-YES-01. None cover a Space with no ontology, or held SQL and the Space join rule.

Keywords: CONNECT-ASK-03, F-TBD-33, unverified_join:no_ontology, no_ontology_join_reason, is_demo_space, file Space, ingest-batch, _run_held_sql, DMS_RECONFIRM_RUN_HELD, DMS_DEF_VALIDATE, DMS_ONTO_FROM_DATA, rule 16, dms-520, ebe65cc

## Main idea

The rule 16 join check ran only when the ask had a Space ontology. A non-demo Space with none was checked by grants and EXPLAIN only. No ontology now means zero verified links: the same allowlist runs with an empty `Ontology()`.

## Expected vs actual (parent `ebe65cc`)

Expected: a two-table join in a Space with no verified ontology abstains.
Actual: `POST /v1/chat/ask` returned `L2_VALIDATED`, `abstained` false, 2 rows (North 180, South 70).

## Repro

1. Create a non-demo Space. Ingest `orders.csv` and `customers.csv` through `POST /v1/studio/ingest-batch`.
2. Stub the generate leg to return a join of `bronze.orders` and `bronze.customers` on `customer_id`.
3. Post the question to `POST /v1/chat/ask`. The parent serves it.
4. Second door: set `DMS_DEF_VALIDATE=1` and `DMS_RECONFIRM_RUN_HELD=1`, ask, then Yes on `POST /v1/chat/ask/confirm`. The parent ran the parked join: `L2_VALIDATED`, "The confirmed query ran.", 2 rows.

`tests/test_connect_ask_03.py` holds both. 19 of its 23 tests fail on the parent source.

## Root-cause class

A guard keyed on "the rule's input exists" (`if space_onto is not None`) reads a missing input as "no rule". The least structured Spaces got the least checking. Four doors had it: the extract loop check, the first generate, the contract ask's `sql_used`, and the held-SQL Yes, which had no join check at all.

## Invariants

- Fix the root-cause class, not the symptom: one helper at every door, not one site.
- Assert the artifact the customer receives: every case asserts the `POST /v1/chat/ask` envelope, text and rows.
- Prove a gate can fail: the parent output is in PR #522.
- Silent fallback is a lie: the first probe abstained with 0 model calls because the reference test stub asserts the ontology `source`, and a file Space is sent the demo context with none. That was the stub refusing, not the product.

## Not verified

- Whether the Platform BIRD Space has a stored ontology. If it has none, its multi-table questions abstain by design.
- Held SQL in a Space that has an ontology: `_run_held_sql` does no join check there. Unreproduced. Routed to the PRD agent, not built.
- No live model, no Postgres (71 control-plane tests skipped).

## Counts seen

Python 3.11.18, DuckDB 2.0.0. Suite, `DMS_SKIP_CONTROL_PLANE_TESTS=1`: 3814 passed, 75 skipped, 34 xfailed, 1601s. Invariants 104 passed. `score_answers --oracle-only` and `score_bird --self-check` exit 0, output identical to the parent.

## Adversary round (same day)

- A join check that raised was HTTP 503 `live_ask_failed` on all four doors, not an abstain. Now `unverified_join:check_unavailable`. Root-cause class: fail closed was implemented as "nothing served", not as "the customer gets the abstain envelope".
- Coverage cost: the allowlist counts table references, so one-table SQL naming its table twice abstains `no_ontology` (cast threshold, `UNION ALL` self, self `EXISTS`, self-join). The bare same-column threshold answers through every door.
- Open, not caused here (F-TBD-39): a CTE alias joined to itself is counted as one table and served with a fanned-out total (400, 1000, 4000 against 250) on `ebe65cc` and on this branch.
- Open, not gated here: a join saved under `DMS_VQ_RAG=1` (provenance `yes:reconfirm`) or registered through `POST /v1/studio/verified-queries` replays as `L0_CERTIFIED`, 2 rows, in a file Space.
- Open (F-TBD-35): held SQL on Yes is not join-checked in a Space that has an ontology.
