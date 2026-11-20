# ONTO-PROFILE-01

PREFLIGHT: PARTIAL. `kb.py search "ontology proposal steward join"` returned no finding on file-Space proposals. F-0025 (claim a ticket first) and A-0003 (value encoding) applied. In this repo `2026-10-28_onto-data-01.md` covers the item lifecycle this builds on.

Keywords: ONTO-PROFILE-01, dms#514, onto_profile, profile_space, store_profile, space_profile_items, ProfileKeyConflict, key_already_approved, DMS_ONTO_FROM_DATA, from_store, file Space, candidate key, orphans, child_has_no_key, ebe65cc

## Main idea

A Space fed by uploaded files answered `derived:false` and nothing was proposed, because a CSV declares no key and the only proposer was a model call. `POST /v1/ontology/data/profile?space_id=` now measures the Space's file-landed tables with no model call and stores candidate keys and links as `proposed` items of source `profile`. Counts, names and landed types only. A profile item belongs to the Space its version names. It is read through `GET /v1/ontology/data/spaces/{space_id}/items` and is left out of the tenant-wide `approved_items()`, so `Ontology.from_store()` never contains it and no answer reads it. The ask path that will read approved items of a Space is ONTO-PROFILE-02, not this ticket.

## Finding 1: an approved item was tenant-wide, so the first approval would leak

- Expected: an item approved for Space A is used for Space A only.
- Actual (parent `ebe65cc`): `approved_items()` filtered by status only on both stores, and `Ontology.from_store()` read all of them. `ontology_versions.space_id` existed and no read used it.
- Repro: `tests/test_onto_profile_01.py::test_control_the_same_items_tenant_wide_do_change_the_answer`. With `DMS_ONTO_FROM_DATA=1`, the same key and link payloads approved outside source `profile` turn the shipments x orders join in a file Space from ABSTAIN `unverified_join` into `L2_VALIDATED` with rows.
- Root-cause class: a scope column that is written and never read. The write looked scoped, the read was global.
- Invariant: assert the artifact the customer receives (rule 10a). The leak is invisible in the store and visible on the `POST /v1/chat/ask` envelope.
- Fix here: source `profile` is excluded from the tenant-wide read. Clause 7 compares the envelope before and after, in every status, flag unset and set.

## Finding 2 (not fixed, routed): a tenant-wide approved link answers a join while its cardinality is `unverified`

- Expected (my reading of rule 16, an assumption): a join answers only over a verified link.
- Actual: in the control above the approved link is loaded by `from_store()` with `cardinality='unverified'` and the join still answers `L2_VALIDATED`. Nothing in that path ran `Ontology.verify`.
- Repro: the same control test, `DMS_ONTO_FROM_DATA=1`, a non-demo file Space.
- Root-cause class: approval treated as verification. A steward's yes is not a measurement.
- Invariant: prove a gate can fail before trusting it green. Rule 16 is written for a SQL-source Space; whether it binds the `DMS_ONTO_FROM_DATA` lane is the founder's call.
- Not changed in this ticket: `ontology.py` and `generative_ask.py` are owned by draft PR #461. Route to the PRD agent before ONTO-PROFILE-02 reads approved profile links.

## Finding 3: two approved keys for one table become the last one

- Expected: one key per object.
- Actual: `Ontology.add_object` overwrites by name. customers has two measured keys (`customer_id`, `name`); approving both would silently keep whichever loaded last.
- Fix here: the second approval answers 409 `key_already_approved` naming the approved item. `approve_version` on a version holding two keys for one table raises before any change. Postgres takes an advisory lock per Space and table so two approvals cannot both pass.
- Root-cause class: last-write-wins on a key that should be unique.

## What the profiler measures

- Key: a column with zero NULLs and as many distinct values as rows. A pair only when no single column qualifies.
- Link: a column of a keyed table whose every non-NULL value exists in a single-column key of another table, identical landed type, compared as landed. Never proposed one-to-one.
- Observation, nothing stored: the same search failing only on orphans (`orphans`), or where the child table has no key (`child_has_no_key`).
- Bounds, all stated in the response: 60 columns per table, pair search over the first 20 NULL-free columns, 2000 link pairs, orphan observation needs at least half of the child's distinct values to match, no self-reference, no composite link.

## Counts seen

Fixture: five seeded CSV files through `POST /v1/studio/ingest-batch` (customers 60, products 25, orders 300 with 5 orphan customer ids, order_items 800 with 796 distinct rows, shipments 120).

- Keys: customers 2, products 3, orders 1, shipments 1, order_items 0. Links: 1 (shipments to orders). Observations: 3.
- `tests/test_onto_profile_01.py`: 25 passed. On parent `ebe65cc`: 24 failed, 1 passed (the control, which is existing behaviour).
- `tests/control_plane/test_onto_profile_01_pg.py`: 4 passed on a throwaway PostgreSQL 16. On the parent it fails to import.
- Two mutations were run and reverted: dropping the `profile` filter from `approved_items()` fails 2 tests; disabling the second-key guard fails 3.

## Not verified

- No live stack: Cortex is a recording fake that executes submits on the lake. No live model, no OpenVault.
- No concurrency test of the Postgres advisory locks.
- `profile_space` lists tables through `bronze.list_bronze_tables`, which goes through `ensure_demo_warehouse` once per process. The measurement itself uses the read-only serving cursor.
- A legacy demo alias id (`sp_q3_audit`, `sp_margin`) stores under its canonical Space id; the items read needs that canonical id.
- Subagents: none used.
