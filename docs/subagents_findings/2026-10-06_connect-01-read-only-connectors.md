# CONNECT-01 - read-only connectors behind one interface

Keywords: CONNECT-01, connectors, introspect_schema, sample, run_select, guard_select, SelectRejected, DMS_CONNECTORS_ENABLED, OpenVault, service_id, sqlglot, Snowflake mock, DR-0005, P-DMS-28, dms-366

## Main idea

`dms_executor.connectors` gives PostgreSQL, MySQL, SQL Server, SQLite, DuckDB, CSV, Excel and Snowflake the same three calls: `introspect_schema()`, `sample(table, n)` and `run_select(sql)`. The layer is OFF by default (`DMS_CONNECTORS_ENABLED=0`), and no API route or Studio surface imports it. A `ConnectionRef` holds an OpenVault `service_id` and no secret. Each call resolves the secret through `POST /keys/services` and drops it after use. Snowflake is tested with a contract-level mock only. Not COMPLETE. Nothing PASS.

## Interface

```python
open_connector(ref: ConnectionRef, *, secrets: SecretsPort | None = None,
               max_rows: int = 1000, timeout_s: float = 30.0) -> ReadOnlyConnector
ReadOnlyConnector.introspect_schema() -> list[TableInfo]
ReadOnlyConnector.sample(table: str, n: int = 10) -> SelectResult
ReadOnlyConnector.run_select(sql: str) -> SelectResult  # columns, rows, truncated, row_cap
```

## Reuse

- PostgreSQL, MySQL and SQL Server connections go through `db_connector.SourceConfig` and `connect()`. Nothing in `db_connector.py` changed, so `test_extract_only` still guards it as before.
- Secrets go through `dms_core.ports.SecretsPort`. This adds no sixth port. The source kind is the swap scenario.
- sqlglot was already a dependency of the executor, so the SQL guard adds no new dependency. sqlglot is now imported in two places: `sql_currency.py` and `connectors.py`. The 2026-09-25 finding said `sql_currency.py` was the only import, and that is no longer true.
- `OpenVaultSecrets.get` makes the same unauthenticated `/keys/services` request that `ManifestMinter.fetch_intermediate` makes. `ManifestMinter` is untouched (#362/#363), and no admin token is sent.

## Guard

The guard runs the dialect's sqlglot tokenizer and then its parser. It accepts exactly one statement, and the root must be a Select, a set operation or a Subquery. Each rejection carries a named code:

- `empty_sql`, `parse_error`, `multi_statement`
- `comment_smuggling` (any comment, including MySQL `/*! */` and `#`)
- `not_select`, `ddl_dml` (for example a DELETE inside a CTE)
- `select_into`, `locking_read`
- `forbidden_function` (file, network, extension and sequence functions, plus Snowflake `SYSTEM$*`)

The engine sessions are a second layer, so SQL that gets past the guard still meets a read-only session:

| Engine | What blocks a write |
|--------|---------------------|
| PostgreSQL | `default_transaction_read_only=on` and `statement_timeout` |
| MySQL | `SESSION TRANSACTION READ ONLY` and `MAX_EXECUTION_TIME` |
| SQLite | `mode=ro` and an authorizer that also refuses ATTACH |
| DuckDB file | `read_only` with external access off |
| CSV / Excel | in-memory DuckDB with external access off and the configuration locked |
| SQL Server | nothing at the engine; pyodbc `readonly` is advisory |
| Snowflake | nothing at the engine; needs a read-only role |

## How each source is tested

| Source | Test |
|--------|------|
| SQLite, DuckDB, CSV, Excel | real engines on `tmp_path` fixtures (`tests/test_connectors.py`) |
| PostgreSQL 16, MySQL 8.4, SQL Server 2022 | CI containers from `docker run`, with a random masked password per run (`tests/test_connectors_live.py`). Locally the module skips unless `DMS_CONNECTOR_LIVE=1` |
| Snowflake | CONTRACT-LEVEL MOCK of `snowflake.connector`. No live account is claimed |
| OpenVault | `httpx.MockTransport`. The real OV is not reached. Live OV `/keys/services` returned 401 on prove (#359 / OV#126) |

## Soft cancel (found by CI on this PR)

MySQL's `MAX_EXECUTION_TIME` stops `BENCHMARK()` at the deadline and returns `0` with no error. A timed-out SELECT therefore came back looking like an answer. `_select` now raises `SelectTimeout` when execute plus fetch ran past `timeout_s`, whatever the engine returned. Real scans on all three engines do cancel at 1s.

The first SQL Server timeout probe was also wrong. A bare cross-join `COUNT` is answered in 0.1s through aggregate pushdown, so that probe proved nothing. The live test now uses a 6-way join with a predicate that spans every table.

Mutation check, run locally: disabling the comment check, the statement count, the SELECT INTO check, the scrub, the SQLite authorizer, the flag default or the deadline backstop each turns at least one test red.

## Open (route to prd-agent)

DR-0005 decides that SQL sources are extract-only, and P-DMS-28 declines live federation. `run_select` sends a caller's SELECT into the customer's engine. The flag keeps that unreachable, but wiring `run_select` into the ask path or Studio needs a founder decision on whether #366 fires the P-DMS-28 unlock. This PR does not decide that. Follow-ons: CONNECT-02 (vector DB read) and ONTO-MAP-01 (cross-source entity mapping).
