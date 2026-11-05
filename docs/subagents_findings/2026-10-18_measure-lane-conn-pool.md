# MEASURE-LANE-CONN-POOL

Keywords: measure_use, measure_confirm, connect_file, connect_serving, CONN-POOL-01, lake_registry, open_lake, _lock_for, serving cursor, ingest wait, dms-283, dms-284

## Main idea

The measure lane was written and tested against a main that opened the lake per call and serialised steward checks with `demo_warehouse._lock_for`. CONN-POOL-01 (#420) replaced that with one pooled connection per lake: a serving read is a shared cursor, a write-mode open waits (`DMS_LAKE_INGEST_WAIT_S`, default 5s) for every serving cursor to return, the asking request's own included. The lane still called `connect_file` (write mode) in its ask-time reads and the steward probe, and still imported the deleted `_lock_for`. Merging main surfaced the import error; the write-mode opens compiled and would have gone green.

## Expected vs actual

- Expected: a confirmed-measure ask reads the lake on the serving side, answers or abstains by name, and never waits on its own request.
- Actual (before): `measure_use.count_rows`, `ingest_pulls`, the filter-value probe and the prune read took a write-mode open while the ask held a serving cursor. The write waits out the ingest window, then the lane abstains "could not be opened". The steward probe also ran compiled SQL on a writable connection and, while it waited, paused every ask on the lake.

## Repro

`tests/test_measure_use.py::test_an_ask_time_read_never_waits_behind_its_own_serving_cursor`: hold `open_lake(lake, write=False)` like an in-flight ask, set `DMS_LAKE_INGEST_WAIT_S=0.5`, call `count_rows`. It fails on the old `connect_file` code and passes on `connect_serving`. Also `tests/test_onto_confirm_01.py` and `tests/test_measure_use.py` patch the renamed seam (`connect_serving`), so a regression to a write-mode open fails their spies.

## Root-cause CLASS

A branch built on a base that has since changed a shared resource contract (connection lifecycle). Green against its own base, red or silently wrong against the merged one. The mypy error on the deleted `_lock_for` was the only mechanical signal; the write-mode opens had none, and only a test that holds a serving cursor shows them.

## Invariant

Two lanes each green against mocks of the other break at the real boundary: after any merge of a shared-resource change, add one test that exercises the real lifecycle (hold the cursor, call the lane), not a spy. Prove a gate can fail: the new test was run against the old code first.

## Ceiling

Only the reads this branch owns were moved. Every other `connect_file` caller on main was already reviewed under #420 and is not re-audited here. The steward probe now takes N serving reads in one request; under a full lease cap it can abstain `serving_lease_cap` (named), which is the intended failure.
