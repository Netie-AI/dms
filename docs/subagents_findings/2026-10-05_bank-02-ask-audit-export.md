# BANK-02

Keywords: BANK-02, audit export, ask_audit, ledger/verify, actor_kind, deployment identity, DR-0004, formula injection, tables_read, executed_trace, redactions, dms-269, dms-267
Main idea: GET /v1/audit/export returns one row per ask (who, question, executed SQL, tables, badge, abstain reason, row count, ledger pointer) as CSV or JSONL, with the ledger/verify result, its scope, the store and the unrecorded-ask count on every row. Nothing recorded asks before, so dms.ask_audit is new and append-only. SQL, tables and row count are what the engine ran, not what the customer was shown. Actor is the deployment and says so. Does not close #269.

## What was already recorded

Nothing. `dms.ledger_ref` holds pointers for `amend.confirm` only. `dms.query_run` is read by `routes/runs.py` and written by nothing. Cortex contract 1.2.0 has append and verify and no read-back of an entry, and the ask envelope is returned and dropped. So no store held an ask's question, SQL, tables or outcome, and the export could not be built from existing records.

## What was added

- `dms.ask_audit` (alembic 0005): SELECT for the app roles, INSERT for steward and admin, no UPDATE and no DELETE grant. RLS by tenant. No hash chain; `cortex_entry_id` is a pointer (hard rule 3).
- `dms_core.control_plane.ask_audit`: the row, a Postgres store and an in-memory store behind one port. This is the catalog family beside `SpaceStorePort`, bound the same way at startup (swap: Postgres or process memory). No sixth port, no new dependency, no config key.
- What ran, not what was shown. The customer envelope drops an abstain's SQL and rows and swaps in a placeholder for a document answer, so the export is not derived from it. `dms_executor.executed_trace` records each statement and its row count where it runs (the engine's own `sql_used` and `rows` before envelope post-processing, and `Executor.submit_sql`). `chat_ask` reads it once after the ask. It is a per-thread side channel, not a key on the envelope, so it never reaches the customer or the scorers. A path that runs SQL locally (bronze sheets, demo) records nothing and the row falls back to the envelope's SQL; a comment-only placeholder is exported as empty. Several statements are kept in order; the row count is the last one's.
- `tables_read` lives in `sql_currency.py`, the one sqlglot module. A name is a CTE reference only where sqlglot scope resolves it to a CTE of an enclosing query, so `WITH sales AS (SELECT * FROM sales) SELECT * FROM sales` reports the base table `sales`. A parse or scope failure takes a FROM/JOIN scan, never an empty list for SQL that names a table.
- `chat_ask` records one row after the outcome, errors included. The ladder after the gate moved to `_answer_ask` unchanged; the gate stays in the route. The MCP ask passes the store through.

## What the export does not prove

- `export_verified` means the Cortex chain verified intact when the file was made, and nothing more: every row and the `X-Audit-Verify-Scope` header say `chain_integrity_only; rows are not matched to ledger entries`. Contract 1.2.0 verifies the whole chain from seq 0 and takes no end or entry id, so it covers the range and everything around it, and there is no entry read-back to match a row against.
- It is false on a break, when the ledger cannot be reached, and (status `incomplete`) when verify checked no entries or fewer than the ledger entries the exported rows point at. An empty ledger verifies trivially and a chain that lost its tail still verifies, so a bare `ok` is not enough. An empty export has no row to carry the mark; the response headers carry it.
- A row has a `cortex_entry_id` only where the ask had a ledger entry or a Cortex receipt. That includes an abstain that got one. An abstain, error or demo row without one has an empty pointer, and an `ans_` answer id is a label, not an entry, so it is exported empty.
- The memory store holds asks since process start and is bounded by a byte budget; when it evicts, the evicted ask counts as unrecorded. `store_backend` is on every row and in `X-Audit-Store`.
- A failed audit write does not refuse the ask. A Postgres write has a connect and a statement timeout and, after a connection failure, a short cool-down so a dead audit database costs a few seconds once, not each ask. A timeout is an unrecorded ask. The store counts them; `unrecorded_asks_since_start` is on every row and in `X-Audit-Unrecorded-Asks`. It is per process and resets when the API restarts, so it can only show gaps since the last start. Whether a failed write should refuse the ask instead is a policy call, not made here.
- The recorded question is capped at 10,000 characters and the SQL at 50,000, cut with a visible marker and `truncated=true`. What the ask accepts is unchanged. The stored SQL is cut; `tables_read` is taken from the whole statement.
- The actor is `DMS_ACTOR_USER_ID` and `actor_kind` is `deployment`. It is not a person until BANK-01.
- Secret removal is a pattern list, not a guarantee. It runs on a normalised view (NFKC, lookalike letters, zero-width characters and block comments dropped) and replaces on the original text, when the row is written and again when it is exported. It covers URL passwords, `password=` and env-style pairs (`DB_PASSWORD`, `OPENAI_API_KEY`, `PGPASSWORD`), JSON keys, `Authorization: Basic`, bearer and provider tokens, base64 blobs and private-key bodies. It cannot recognise an arbitrary string as a secret, and an unquoted password value is taken to the end of its sentence. It does not rewrite SQL identifiers, numbers or column comparisons. `redactions` on each row counts what was replaced, so an auditor can see that text was altered.
- The append-only guarantee holds for the app roles only. The table owner and a superuser can still UPDATE, DELETE or TRUNCATE, a tenant delete cascades, and compose currently runs the API as `POSTGRES_USER=dms`, a superuser. That is a BANK-04 item; compose is not changed here.
- CSV joins `tables_read` with `;`, which a semicolon-locale spreadsheet may split into columns on import. JSONL carries an array.
- `L2_ANOMALOUS` and demo answers export `badge=validated`; `badge_level` and `ask_mode` tell them apart.

## Open

- Alembic: PR #316 also descends from 0004 and adds 0005_onto_snapshot and 0006_onto_measure_confirm. With both there are two heads. Whichever PR merges second must chain after the other's last revision and renumber. CI sets DMS_SKIP_CONTROL_PLANE_TESTS=1, so CI will not catch it.
- `audit.export` is a new F5 task id. If Cortex's catalog lacks it the gate returns `gate_task_unknown` and `mutation=False` lets the export through, and verify is called under that read gate while `POST /ledger/verify` enforces the mutation gate. Open for the epic.
- Root cause of the OpenVault probes in the test suite predates this ticket: importing `dms_api` runs a module-level `create_app()` that starts an Executor, which probes OpenVault. The new test file reports the probe offline per test, after import; it does not make the module vault-free.
