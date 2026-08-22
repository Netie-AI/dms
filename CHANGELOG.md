# CHANGELOG

Append-only. Never edited, only added to. Newest first.

## 2026-08-22 - S4 warehouse identity

- **Two DuckDB files were the remaining S4 gap** (TAS-DMS §6, measured
  2026-08-02). Studio ingest writes `DMS_WAREHOUSE_DB` (`data/dms_demo.duckdb`).
  Cortex answers from `CORTEX_HOME/data/dms_demo.duckdb`. An uploaded sheet was
  unreachable from `POST /v1/chat/ask` — a silent miss. dms#4/#5 (receipt
  honesty, grounding) stay closed; this is the warehouse-identity leftover.
- **Fix is an explicit bronze copy, not one file.** Demo seed uses
  `txn_type='outbound'`; the engine file uses `'OUT'`. Pointing ingest at the
  engine warehouse reseeds and drops its extra tables. `sync_bronze_to_serving`
  copies bronze user tables only.
- **Regression:** `tests/test_warehouse_identity.py` fails if an xlsx lands in
  ingest and serving cannot see it; `--check` exits 1 on that diverge.
- **Demo step:** `python scripts/sync_bronze_to_serving.py` (Start-DMSStack
  runs it before Cortex starts, while the serving file is unlocked).

## 2026-08-06 - working-tree recovery, SCORE-03, demo 31/31

- **Composition root recovered.** `apps/api/dms_api/wiring.py` had been
  truncated to zero bytes. It is the only module allowed to import
  `dms_executor` (`.importlinter`), so every route reaching the executor died at
  import and seven test modules failed to collect. Restored and extended with
  `reveal_origin_uri`, `search_document_chunks`, `list_document_chunks`;
  `warehouse_tables` now takes `space_id`. Chunk search resolves the Space
  filter in SQL, not by post-filtering rows.
- **RAG-01/02/04/05 + REVEAL-01 landed** with the `0003_document_chunks`
  migration, the L2 bakeoff record, the demo runbook, and the playground bank.
- **SCORE-03 (#42).** `f32_ambiguous_categoty_top3` — no sheet named, "categoty"
  left misspelled; fixture carries Sales truth (Electronics 1,545,366.40 / Home
  1,199,018.49 / Misc 380,948.33) against the Wide_Fill ranking the live stack
  returned under green (Home 383,803.56 / Sports 242,755.97 / Misc 228,548.84) —
  wrong rank *and* wrong magnitude. Plus `blank_hanging_rows_top3`: messy sheet
  must equal `Sales_Clean`. Both traps self-check inside `score_answers` and
  exit 1 rather than report green having tested nothing. Falsified per R-0007.
- **Two silent skips removed (R-0002).** `test_resolve_oracle_on_shipped_hostile_fixtures`
  returned early when the fixture dir was absent; `test_playground_pack`
  asserted ids and keys the bank never had, so it was testing nothing that
  existed. Now asserts ladder coverage L0-L5 and unique ids.
- **Demo verified live: 31/31** (`verify_demo_live.py`), twice consecutively.
- **#43 DEMO-COLD-01 filed.** The *first* run against a cold stack refused the
  freshly uploaded file as `grounding_not_grantable`; warm re-runs pass. Demo's
  own happy path. Candidate mechanism is the warehouse-read swallow at
  `demo_grants.py:90-98` turning an unreadable warehouse into a permission
  decision — unconfirmed, and not fixed here (unrouted product change).

## 2026-08-05 - Wave 7 land + hard-rule-12 + EPIC-019 start

- **E9-02 (#41 open — verify pending).** Ambiguous multi-sheet category ranking
  demotes at `build_answer_envelope` when competing Sales vs Wide_Fill (or
  cross-file sales) scopes disagree and the ask is not uniquely scoped.
  Uniquely scoped / single-grounded executed ranks still certify. F32 fixtures
  in `tests/invariants/test_envelope.py`.
- **E9-01 (#34 CLOSED).** Invent-totals demote on ask map path; CLAUDE rule 10a
  is E1-E9. Empty executed SQL result demotes to ABSTAIN (hard rule 12 —
  BETA/SKU-BETA empty-filter green). `INVARIANT-CHANGE` in envelope tests.
- **SCORE-01/02 (#36/#37 CLOSED).** Hostile 9-case pack +
  `tests/fixtures/hostile_score` + `--help` / DEMO_RUNBOOK live-stack recipe.
- **Cortex value-norm.** `city` in `VALUE_COLUMNS`; KL→Kuala Lumpur acronym
  ladder; literal_normalize unique cross-column resolve when hint missing.
- **VQ-01 start.** Certified assets accept curated `synonyms:` (exact normalize
  only). EPIC-019 filed #38; children #39/#40.
- **Playground.** `playground/` sample data + 13 mutable questions +
  `scripts/playground_ask.py`. L4/L5 are aspiration labels only (P-DMS-33).


## 2026-08-02 - demo-eve P0 sweep

- **P0-DEMO-01 (#4) fixed.** The first `.xlsx` into a fresh warehouse reported
  `ingested=0, parse_error:... _ingest_registry does not exist` while the rows
  were already in bronze. The registry is created before any path that renames a
  table into place, and the swap plus the registry write are one transaction, so
  no failure after the rename can produce a receipt the warehouse contradicts.
  Row counts are read back from the created table. Fixture 15 is the first
  non-CSV ingest fixture; all 14 before it were CSV, which is why this shipped.
- **ACL-01 (#2) fixed.** `live_ask` minted from `demo_acl()`, which allowlisted
  every demo table regardless of `space_id`. The DR-0002 grant split is now
  seeded in `dms_executor.demo_grants` behind the `SessionStore` port, so the
  boundary holds without Postgres. Wiring it exposed a second leak: the bound
  session id varied by grounding scope but not by Space, so switching Space in
  one chat was served under whichever manifest bound first.
- **P0-DEMO-03 (#5) fixed.** Grounding on an uploaded table widened the manifest
  to all six demo tables while the UI read "Grounded in 1 file". Uploads are
  grantable from the ingest registry, and a selection that cannot be granted is
  refused by name rather than dropped. The envelope now carries
  `grounded_tables`, so the count a viewer reads comes from the minted manifest.
- **Demo Spaces renamed** to DR-0002's `Finance` and `Warehouse Ops`.
- **Space refusals render as answers**, not raw `path_not_allowed` 403s.
- **`Start-DMSStack.ps1` binds Postgres to the host** via the hostdb overlay;
  without it the container was healthy and unreachable, the API fell back to the
  in-process Space store, and 18 control-plane tests errored instead of running.
- **CI-02 (#3)**: workflow now reads `CORTEX_CONTRACT_TOKEN`. Still 404 - the
  token itself cannot see `Netie-AI/Cortex`.

Verified live (`scripts/verify_demo_live.py`): 18/18 against DMS + Cortex +
OpenVault. Full corpus 188 passed.
