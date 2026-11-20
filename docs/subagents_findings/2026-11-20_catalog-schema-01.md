# CATALOG-SCHEMA-01: the catalog read the demo's schema, and a bare-name grant check listed a twin

Date: 2026-11-20. Repo: DMS. Ticket: #518. Base: `claude/integration-epic-178` at `583377f`.

## Expected vs actual

Expected: a Space made from uploaded files sends Cortex a `schema_context` naming its tables and columns, and nothing else.

Actual on the base, reproduced (blocker 8 of `2026-11-13_dms-first-live-answers-on-new-data.md`, Cortex tree):

1. `read_catalog` read schema `main` only. Uploads land in `bronze`. The catalog was empty, the fingerprint was `""`, and the Insights body carried no `schema_context`.
2. Not in the ticket, found by the first test: with a `main` table that has the same bare name as an upload, the file Space was shown the `main` table. `relation_gap("orders", {"bronze.orders"})` is granted, which is right for SQL (a bare name resolves through the grant) and wrong for a catalog row that is `main.orders`. The prompt on the base read `- orders ... - main_only_marker VARCHAR` for a Space granted only `bronze.orders`.

## Repro

`tests/test_catalog_schema_01.py` on `583377f`: 16 of 23 fail. Ingest two CSV files through `POST /v1/studio/ingest-batch` into a non-demo Space with `DMS_SCHEMA_CONTEXT=1`, replace the Insights HTTP client, post `POST /v1/chat/ask`, read the captured body.

## Root-cause class

1. A read scoped to the demo's schema. Built and tested on `main`, a no-op where customer tables live. The flag being on gave no hint.
2. One grant-key function used for two questions. "May this SQL name resolve?" and "is this stored table granted?" differ when a bare name exists in two schemas.

## Fix

The schema set is bound from the granted names. A catalog row is judged only against the grants of its own schema. A `main` table keeps its bare name and its bytes (golden recorded on the base). Any other table is named `schema.table`, and the value readers quote schema and table apart.

## Invariants that applied

- Assert the artifact the customer receives: the wire body and the `POST /v1/chat/ask` envelope, not the function alone.
- Prove a gate can fail: 16 red on the base, 7 of 7 mutations red.
- Adversary is not the verifier: three Sonnet agents, none wrote the fix.
- A control that blocks legitimate work is a failure: the pinned `dayname` false abstain (base defect, #527 rule) is now reachable in file Spaces.

## Adversary round (agent shape)

Three Sonnet agents in isolated worktrees, one lens each: an ungranted table in a granted schema, two Spaces or schemas with same-named tables, value normalization and identifier quoting. Isolated context, shared harness (`_file_space`, `_two_schema_lake`). The worktrees started on `main`, not on the branch: each agent noticed and checked out the fix commit. Say the commit in the brief next time. Lead on the session model for the fix and the mutation run.

No table shown and no figure served outside the grant. One defect of this change, fixed: a grant written `.orders` fell back to `main`. 130 of their cases are kept in `tests/test_catalog_schema_01_adv_*.py`.

## Open, older than this change (found by the adversaries, not fixed here)

1. `note_serving_source(db, space_id)` is called with no grants after every ingest (`bronze.py`, `db_connector.py`), so the Space's in-memory index and its disk record hold every `main` table until the first ask. Not on the wire: `lookup_schema` overlays only on a fingerprint match. Fix: pass the Space's grants at the two callers.
2. `lookup_schema` leaves the old in-memory index in place while a rebuild for a narrower grant runs. Memory only.
3. `ask_payload._read_values` quotes a dataset name as one identifier, so `build_dms_payload` returns None for a qualified grant. No caller today. PR #455 must use `_quote_relation`.
4. A column named with a reserved word (`select`, `order`) is printed bare in the prompt. A table `bronze.select` is granted but not listed (`relation_gap` says `sql_relation_unresolved`). Both fail safe.
5. A filter hint shows the question's casing (`= web`), not the stored value (`WEB`). The gate abstains `unhonored_qualifier` when the model copies it.
6. Two uploads whose names differ only by case (`Extra.csv`, `EXTRA.csv`), or the same filename in two Spaces, share one `bronze` table. The later ingest replaces the rows and the first Space loses its grant. Nothing crosses Spaces. The first Space loses data with no warning.
7. `_serving_file` maps space ids that differ only in punctuation to one record file. Ids are UUIDs today.

## Not verified

A live model, a live Cortex, Postgres control-plane tests, Excel sheet names, an unticked upload.
