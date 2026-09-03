---
status: accepted
date: 2026-09-03
decision-makers: founder, delegated to the Claude Code lane on 2026-09-03 ("these two I leave the decisions to you, completely solve them") - PRD-001 feedback ledger F36 and F37
outcome: F36 resolves as path (a). F27 stands. EPIC-020 becomes wave foundation. F37 approved as EPIC-024.
---

# DR-0005 - Extract-only is the answer to SQL sources, and the customer changed

## Context and Problem Statement

F36 arrived on 2026-08-07 as one sentence carrying two products: *"our main users are
companies with sql servers like microsoft sql and mysql ... so we need ingest data then
perform query immediately or if best we become a plugin that can be called and appear in
people's microsoft sql server."* The PRD Agent split it four ways and could slice only
one. It sat for eighteen days as the critical-path blocker on the entire "point it at a
customer database" path, because three of the four parts were founder decisions.

On 2026-09-03 the founder delegated both F36 and F37 to this lane, and in the same
message described what the first paying client actually wants:

> source data from microsoft sql and mysql ... full pipeline clean, understand and collect
> and arrange the data, then just call api for explaining the data like a RAG or a sql
> creator for database

That description decides part (3) on its own. It is a **pipeline that pulls data out and
serves it through an API** - the client continues to pay Microsoft for the database and
pays Netie for what sits beside it. It is not a plugin living inside SQL Server.

## The four parts, decided

**(1) Target customer - Tier 3 amendment.** Applied. PRD-001 section 1 said *"Malaysian
logistics and distribution SMEs run on Excel"* and section 2 said *"Excel and CSV today."*
The first client runs MSSQL and MySQL. The press release now names both shapes; the FAQ
answer names the extract. The Excel path is not removed - a customer who runs on
spreadsheets still exists - it stops being the only customer.

This is a tier the agent system says agents may never author (`AGENT_SYSTEM.md` section
8). It was authored under explicit founder delegation, and this record is where that is
written down so the audit trail does not read as drift.

**(2) "Ingest then query immediately" - EPIC-020, re-prioritised.** Not new scope. EPIC-020
leaves last-in-Wave-7 and becomes **wave foundation**, tier 1, in flight now. "Immediately"
means *after extraction lands in bronze*, which on a sub-second extract of a normal SME
table is immediate enough, and which keeps every answered number carrying row-level
provenance. It does not mean querying the customer's database live - see (3).

**(3) "Plugin inside SQL Server" - F27 stands. Declined again.** The founder closed F27
YES-to-declining on 2026-08-05 (`P-DMS-28`) and nothing has changed the arithmetic:

- Live federation degrades the Space boundary to advisory. A Space is a sandbox over
  selected sources with row-level provenance on every answer. A query that runs inside
  the customer's engine is a query whose rows DMS did not see land, and whose provenance
  is the customer's word.
- `duckdb.execute` stays inside `packages/executor` (CLAUDE.md hard rule 7) only if the
  data comes to the executor. Federation moves execution to the customer's engine and
  the invariant has nothing left to guard.
- The vendor-agnostic property the founder wants - MySQL today, Postgres next year, the
  ontology does not break - **exists only under extraction**. An ontology compiled over
  bronze survives a source swap. A plugin inside SQL Server is a plugin inside SQL Server.
- The parked connector already refuses the shape: it never uses DuckDB's `ATTACH` or
  `INSTALL` scanners, so the hostile-SQL guard in `dms_executor.manifest` keeps rejecting
  those statements on this path too.

`P-DMS-28`'s unlock condition ("a PRD amendment explicitly reopens live federation +
Cortex ownership of ontology") has not fired. This record does not fire it.

**(4) Provenance for a non-file source - defined here, no epic.** A file-backed bronze
table's origin is its path and content hash. A SQL-backed bronze table's origin is:

```
source        sqlserver://host:port/database#schema.table   (SourceConfig.describe(),
                                                               credential-free by construction)
extracted_at  UTC timestamp of the pull
row_count     rows landed
truncated     true if the pull hit max_rows and the table has more
ref_id        the _src ref every landed row carries
```

Every row already carries `_src STRUCT(ref_id, row)[]` and `_ingest_id` through
`write_bronze_rows`. What the parked connector did not do is write the **registry row**
that names the source - `bronze._ingest_registry` records files, and a SQL pull is not a
file. EPIC-020's first ticket adds it. Until then a SQL-sourced table has row provenance
and no source provenance, which is half an answer and must be reported as such.

## F37 - approved as EPIC-024

*"Involve a basic ETL tool or something like at least visualisation to let ppl to see how
they change from bronze to silver to gold."* The PRD Agent's routing holds and is adopted
whole: this is a **rendering epic, not an ETL tool**. `PromoteReceipt.to_dict()` already
returns `source_rows, passed, quarantined, unmatched, reconciled, counts_by_reason` on
every promote and the UI has never called it. The surface is **Library**, which already
models the three tiers as folders; Studio keeps the promote action because it is a gated
mutation.

**Do not build an arrow diagram.** dbt renders the arrow for free. The differentiated view
is `source_rows -> passed + quarantined`, `counts_by_reason`, and `unmatched` - negative
means join fan-out - which is the instrument that caught a ~15x oracle inflation in live
use. That is the "understand" step of the client's pipeline made visible.

F36 and F37 **reinforce under (a)**: a bronze table extracted from MSSQL is a snapshot of a
table the customer already knows by name, so the bronze-to-silver delta becomes legible
for the first time.

## WIP

EPIC-020 (tier 1, invisible) pairs with EPIC-024 (tier 4, visible), which satisfies the
rule that at least one in-flight epic is human-inspectable. EPIC-003 and EPIC-017 move to
**queued** - never closed, that orphans their children. EPIC-003's three P0 tickets closed
this week; EPIC-017's remaining item (#59) is Cortex-side.

## Consequences

The first client can be served from the existing architecture with one connector and one
bridge. The bridge is the part that matters: the parked connector reads
`INFORMATION_SCHEMA.TABLES` and never the keys, while `scripts/ontology.py` compiles from
declared primary and foreign keys and refuses any link whose cardinality it has not
measured. "Collect" exists; "understand" exists; nothing joins them. EPIC-020's second
ticket is that join, and it is the smallest high-value change in the estate.

What is given up: any customer who genuinely needs the engine to run inside their
database. That customer is not the first client, and P-DMS-28 says what would reopen it.

## Confirmation

**Exists:** `tests/test_db_connector.py` (14 tests, fake DB-API, no driver needed in CI) -
credential hygiene, identifier safety from the server's own catalog, the OOM cap, rows
landing in bronze with provenance.

**Must exist before EPIC-020 reports COMPLETE:**

- an invariant that `db_connector` never imports a DuckDB scanner (`ATTACH`, `INSTALL`,
  `sqlite_scanner`, `postgres_scanner`, `mysql_scanner`) - the mechanical statement of
  "extract-only", proven able to fail
- a test that a SQL-sourced bronze table carries a registry row naming its source, and
  that `preview` renders that source rather than a blank
- the ontology bridge test: keys read from the source's catalog, compiled, verified, and a
  link the source declares but the data violates is **refused**, not compiled

**Does not exist and is not claimed:** any run against a real SQL Server or MySQL. Every
test in this slice runs against a fake DB-API connection. The first live extract against
the client's database is a verify-agent task on a live stack, not a unit test.
