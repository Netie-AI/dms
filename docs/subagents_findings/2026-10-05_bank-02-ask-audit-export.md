# BANK-02

Keywords: BANK-02, audit export, ask_audit, ledger/verify, actor_kind, deployment identity, DR-0004, formula injection, tables_read, dms-269, dms-267
Main idea: GET /v1/audit/export returns one row per ask (who, question, executed SQL, tables, badge, abstain reason, row count, ledger pointer) as CSV or JSONL, with the ledger/verify result on every row. Nothing recorded asks before, so dms.ask_audit is new and append-only. Actor is the deployment identity and says so. Does not close #269.

## What was already recorded

Nothing. `dms.ledger_ref` holds pointers for `amend.confirm` only. `dms.query_run` is read by `routes/runs.py` and written by nothing. Cortex contract 1.2.0 has append and verify and no read-back of an entry, and the ask envelope is returned and dropped. So no store held an ask's question, SQL, tables or outcome, and the export could not be built from existing records.

## What was added

- `dms.ask_audit` (alembic 0005): SELECT for the app roles, INSERT for steward and admin, no UPDATE and no DELETE grant. RLS by tenant. No hash chain; `cortex_entry_id` is a pointer (hard rule 3).
- `dms_core.control_plane.ask_audit`: the row, a Postgres store and an in-memory store behind one port. This is the catalog family beside `SpaceStorePort`, bound the same way at startup (swap: Postgres or process memory). No sixth port, no new dependency, no config key.
- `tables_read` in `sql_currency.py`, the one sqlglot module, re-exported through `dms_executor` so the import contract holds. A parse failure takes a FROM/JOIN scan, never an empty list for SQL that names a table.
- `chat_ask` records one row after the outcome, including errors. The ladder after the gate moved to `_answer_ask` unchanged; the gate stays in the route. The MCP ask passes the store through.

## What the export does not prove

- `export_verified` means the Cortex chain verified intact from seq 0 when the file was made. The contract takes no end and no entry id, so it is the whole chain, not the range. It does not prove a row here matches a ledger payload, because there is no entry read-back.
- A break, or a ledger that cannot be reached, is unverified. An empty export has no row to carry the mark; the response headers carry it.
- Only an ask that had a ledger entry has a `cortex_entry_id`. Abstain, error and demo rows have none. An `ans_` answer id is a label, not an entry, and is exported empty.
- The memory store holds asks since process start. `X-Audit-Store` says which store served the file.
- A failed audit write does not refuse the ask. The store counts it and the export reports `X-Audit-Unrecorded-Asks`. Whether it should refuse instead is a policy call, not made here.
- The actor is `DMS_ACTOR_USER_ID` and `actor_kind` is `deployment`. It is not a person until BANK-01.
- Secret removal is a pattern list (URL passwords, `password=`, bearer and provider tokens, key blocks). It cannot recognise an arbitrary string as a secret. It runs when the row is written and again when it is exported.

## Open

Alembic 0005 and PR #316 (0005_onto_snapshot) both descend from 0004. The later merge re-points `down_revision`.
