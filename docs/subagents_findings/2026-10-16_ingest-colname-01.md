# INGEST-COLNAME-01

Keywords: INGEST-COLNAME-01, quote_ident, write_bronze_rows, contract_infer, multi-statement execute, COPY TO, ATTACH, column name injection, adversary, F-TBD-13, dms-277

## Main idea

A column name from a connected SQL source is data, and it was used as SQL. `write_bronze_rows` built `"{c}" VARCHAR` with no escaping of `"`, and DuckDB `execute` runs a multi-statement string, so a column name could create a table, `COPY ... TO` a file, `ATTACH` a database or `read_csv` a local file on the lake. `contract_infer` re-quoted the landed names the same raw way. One helper, `bronze.quote_ident`, now covers every sink, and the writer quotes before it opens the lake.

## Expected vs actual

- Expected: any column name lands as that name, or the ingest refuses it by name; nothing in the name runs.
- Actual (before): a source column named `x" VARCHAR); CREATE TABLE main.pwned_by_colname AS SELECT 42 AS v; --` ingested with status 200 and the lake then held `main.pwned_by_colname`. A name with one double quote was a raw `ParserException`.

## Repro

`tests/test_ingest_colname_01.py` (7 tests): `POST /v1/studio/sources/sql` with a fake connection whose second column is the payload, then assert the lake's table list, the landed rows and, for `POST /v1/pipelines/infer-contract`, that the `COPY ... TO` exfil file does not exist. Disable `quote_ident`'s quote doubling (5 red), `contract_infer`'s use of it (1 red), or the writer's use of it (6 red).

## Root-cause CLASS

Untrusted identifier interpolated into a multi-statement execution surface. Fixing the writer alone moved the statement to the reader (`contract_infer`), so the fix is one helper for every sink of a source-controlled name, not a patch at the point of the report.

## Invariant

Fix the root-cause class, not the symptom in front of you. Assert the artifact the customer receives (the lake's tables and rows, the exfil file), not the SQL. Prove a gate can fail.

## Ceiling

Control characters and empty names are refused (empty, NUL) or land under their own quoted name; a legal-name rename on the receipt exists only on the measure-lane branch. A column named `_src` still displaces the row provenance column (parked as P-DMS-56 on that branch). Threat model is a compromised or hostile source database; not verified whether any install connects a source whose column names an outsider controls.
