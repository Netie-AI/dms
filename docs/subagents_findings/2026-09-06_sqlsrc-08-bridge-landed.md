---
keywords: [SQLSRC-08, dms-156, dms-157, EPIC-020, ontology, verify_source_links, source_links, extract-only, DR-0005, R-0004, R-0007, R-0003]
main_idea: The semantic layer moved into dms_executor and POST /v1/studio/sources/sql now measures every declared link on the receipt. Bench unchanged 896/494/811/0. First draft put duckdb in db_connector and test_extract_only caught it - the fix was to move the code, never the gate. Not verified by this run (R-0003); #116 re-run is the verifying step.
---

# SQLSRC-08 landed: the refusal now has a path to the customer

**Date:** 2026-09-06
**Branch:** `feat/sqlsrc-08-ontology-on-the-route` (pushed)
**Tickets:** Netie-AI/dms#156 (this), #157 (the claim that is still missing)

PREFLIGHT: HIT on `2026-09-06_ontology-not-on-the-route.md`, which is the finding
this closes the path half of. Reuses `2026-09-06_capped-parent-orphans.md`.

## Golden rule

> An invariant that fires on your first draft is the invariant working. The fix
> is the code's location, never the gate's threshold. `tests/invariants/**` is a
> protected path in this repo precisely so that reaching for the second option
> costs a commit-message declaration - and if you find yourself wanting one, the
> design is wrong, not the check.

## What landed

- `scripts/ontology.py` -> `packages/executor/dms_executor/ontology.py`.
  `scripts/ontology.py` remains as the CLI entry. `ROOT` went `parents[1]` ->
  `parents[3]`; carrying the old arithmetic across is the silent break, because
  it fails at manifest-read time and not at import.
- `packages/executor/dms_executor/source_links.py` - `verify_source_links()`.
- `apps/api/dms_api/wiring.py` puts its result on the `POST /v1/studio/sources/sql`
  receipt as `links`.

## The one that caught me (worth the finding on its own)

The first draft put `verify_source_links` in `db_connector.py`, which needed
`import duckdb` and a `duckdb.connect` there. Three
`tests/invariants/test_extract_only.py` tests went red:

```
The connector must extract, never federate (DR-0005). A scanner or a DuckDB
handle on this path runs the customer's query in the customer's engine and
the Space boundary degrades to advisory:
  db_connector.py:26 import duckdb
  db_connector.py:659 duckdb.connect
```

The invariant is right and the reasoning is not about tidiness. Fixed by moving
the function to its own module (R-0004), leaving `db_connector` at zero duckdb
references. The split turned out to be the honest one anyway: `db_connector`
talks to the source database, `source_links` talks to what landed in bronze.

Note the third failing test - `test_the_probe_shapes_are_not_already_in_the_connector`.
That is the guard on the guard: it asserts the probe strings are not already
present, so the check cannot pass by looking for something that is always there.
Same shape as the `#73/#74` "a second test guards the guard" row in STATUS.

## Verify that ran

- `python scripts/ontology_bench.py` -> **896 cases, 494 shapes, 811 answerable,
  precision 100.00 pct, 0 wrong**, corrupted-link self-check caught. Byte-for-byte
  the pre-move corpus (R-0005 - nothing valid became refused).
- `pytest tests/test_sql_source_route.py tests/test_db_connector.py
  tests/test_sql_source_watermark.py tests/invariants -q` -> **111 passed**.
- `lint-imports` -> **3 kept, 0 broken**, 172 files analysed (was 171 - the layer
  is inside the linted packages for the first time).
- R-0007, twice, once before and once after the module move: hardcoding a clean
  bill at the wiring call turns exactly the two new receipt tests red.

## Two reds that pre-date this work

Reproduced at `2d0455ff9` in a detached worktree, before any commit of this
session:

- `tests/test_cca_geo.py::test_certifies_against_iso2_encoding_and_filters_in_that_encoding`
  asserts `("MY","SG","TH")` and gets a different permutation each run
  (`("SG","MY","TH")`, `("MY","TH","SG")`). Nothing guarantees the order. Same
  class as the lake-schema flake fixed earlier the same day: the flake is the
  defect reporting itself (R-0002).
- `tests/test_pipeline_receipts.py::test_writer_held_lake_is_busy_not_empty` -
  "writer subprocess never took the lake lock" inside 10s.

Neither is caused by SQLSRC-08. Both are still failing tests (R-0002) and neither
has a ticket.

## Do not

- Do not treat this as verifying #116. **This run wrote the code, so this run may
  not certify it** (R-0003). The #116 re-run against the live `sqlsrc06-mssql` and
  `sqlsrc06-mysql` containers is the verifying step and belongs to a different run.
- Do not report EPIC-020 COMPLETE. Clause 2 still fails on orphans until #157,
  and #116's ask block is blocked on the sealed OpenVault (founder only).
- Do not claim `POST /v1/chat/ask` consults the ontology. It does not, and that
  is not EPIC-020 scope.
- Do not claim a rate from any of this. n = 1 schema family. R-0010.
