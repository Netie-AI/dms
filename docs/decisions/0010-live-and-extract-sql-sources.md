---
status: accepted
date: 2026-11-27
decision-makers: founder (F1, 2026-11-27 18:04 SGT and 18:05 SGT), recorded by R2-CONNECT-CORE-01
outcome: DR-0005 is superseded. A SQL source is live or extract, per source. The user picks. DuckDB scanners stay forbidden.
---

# DR-0010 - A SQL source is live or extract, and the user picks

## Context and Problem Statement

DR-0005 (now superseded) said a SQL source is extracted and never queried live.
The founder decision F1 on 2026-11-27 18:04 SGT is that a saved connection is
queried live, read-only. At 18:05 SGT the founder added: both live and extract,
and the user picks per source. The AI may choose extract for a heavy repeat
analysis. There is no hard row cap and no fixed query timeout. Full results
come back by streaming, by pages, or by a server-side export. The only stops
are a user-visible Cancel and a runaway guard: a cost or plan estimate that
warns the person. It never truncates in silence.

P-DMS-28's unlock was a PRD amendment that reopens live reads. This record is
that founder decision, written here because the ticket requires the code and
DR-0005 to stop disagreeing. The PRD text itself stays with the PRD agent.

## Decision

A connection is stored in Postgres (`dms.connect_sources`) and survives a
process restart. The secret stays in OpenVault. DMS stores `cred_ref` only.
The OpenVault value is a JSON object `{"user": "...", "password": "..."}`.
DMS reads it in memory for the call and does not log it, audit it, or put it
in an error.

The user sets `mode` to `live` or `extract` on save. Missing mode is refused.
It is not guessed. Extract keeps the existing bronze pull for the landed copy.
Live runs the statement on the source through that source's own driver.

Live execution:

- The session is read-only where the driver allows it (SQLite `mode=ro`,
  Postgres `SET TRANSACTION READ ONLY`, MySQL and MariaDB
  `START TRANSACTION READ ONLY`, SQL Server `readonly` on the driver).
- The statement must be one query. A second statement, a write, `SELECT INTO`,
  and a row lock are refused before anything is sent. The per-query grant
  check is the existing structural allow-list (`serve_gap`): every relation
  has to be a granted table.
- There is no row cap and no statement timeout. A page is a transport window.
  Export streams until the driver is done. `truncated` is false. `bounded` is
  false. Cancel stops the stream and says so.
- Before execution, the driver explains the plan. A missing estimate, or a
  cost at or above the runaway constant, returns a warning and does not run
  until the caller sends `confirm_runaway`. That warning is not a truncation.

On save, DMS introspects the source and writes a grant row for every listed
table, granted by default. A steward can narrow those rows. The save also
inserts `dms.connect_onto_jobs` with status `requested`, which is the trigger
for R2-AUTO-ONTO-01. This record does not build that ontology.

DuckDB `ATTACH`, `INSTALL`, and `*_scanner` stay forbidden. Live reads do not
go through a DuckDB scanner. `tests/invariants/test_extract_only.py` is
unchanged and still guards that. The handshake has a short connect timeout so
a dead port cannot hold a worker. That timeout is not applied to the query.

The whole path is off unless `DMS_CONNECT_API` and `DMS_CONNECT_V2` are on.
Off, the connect book stays process memory and the ask path does not change.

## Confirmation

`tests/test_connect_v2.py` is the confirmation: the flag-off route stays on
the old allow-list, a connection row survives a new app process, the test
endpoint reports ok and typed failures (`auth`, `network`, `permission`,
`tls`) against Postgres and MariaDB, a canary secret is absent from the
response, the log, and the audit, and a paged read plus an export return
every row with `truncated` false.
