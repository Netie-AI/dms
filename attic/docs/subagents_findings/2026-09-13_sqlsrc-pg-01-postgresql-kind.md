---
keywords: [SQLSRC-PG-01, postgresql, SqlSourceIn, db_connector, SqlSourcePanel, dms-172, EPIC-020b]
main_idea: "kind=postgresql is a first-class SQLSRC extract. Catalog must not treat database as schema (MySQL). Platform attaches bird_minidev after merge. Not SCORE-BIRD PASS."
models: [grok-4.6]
workflow: ticket-runner
reuse: golden_rule
status: raw
cite: agent: this-goal
repo: DMS
date: 2026-09-13
---

# SQLSRC-PG-01 postgresql kind

PREFLIGHT: PARTIAL (sqlsrc-09-studio-form; sqlsrc-08-bridge-landed)

## Main idea

`POST /v1/studio/sources/sql` 422'd `kind=postgresql` because `SqlSourceIn.kind` and `SourceConfig` were `sqlserver|mysql` only, and `connect()`'s else branch was MySQL. PostgreSQL Mini-Dev tables live in `public` while the database is `bird_minidev`; the MySQL `TABLE_SCHEMA = database` filter would extract zero tables.

Widen kind, default port 5432, ANSI INFORMATION_SCHEMA like SQL Server, `psycopg` lazy import, Studio radio, same password/receipt/verify laws. `kind=postgres` stays 422.

## Not this ticket

Platform attach of `bird_minidev` @ `127.0.0.1:5432` into BIRD Space `f0da7dd3-58b3-4d15-84a8-a18f2853ed87`. SCORE-BIRD-01. Reopen EPIC-020 #108 COMPLETE. EPIC-008 COMPLETE.

## Verify

```
pytest tests/test_db_connector.py tests/test_sql_source_route.py tests/invariants/test_extract_only.py -q --tb=short
cd apps/ui && npm test -- src/lib/sqlSource.test.ts src/components/SqlSourcePanel.test.ts
```
