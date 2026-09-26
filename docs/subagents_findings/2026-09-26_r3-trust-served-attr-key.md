# R3 trust: served attribution dropped on fallback, unkeyed Cortex reach (dms#305, dms#273)

Keywords: SERVED-ATTR-01, KEY-01, served_provider, served_attribution, cortex_key_missing, demo key, dms-305, dms-273, dms-261, dms-258
Main idea: Two fail-open classes on the trust surface. Attribution fields were copied only on the generative path, so the contract-ask fallback after a generate call shipped with no served_* and no marker. And a published demo key was the settings default, so an unconfigured DMS still reached Cortex.

## Finding 1: served_* vanished after a generative miss (dms#305)

- **Expected:** every envelope from an ask that made a generate call carries the call's served_provider/served_model, or says attribution is missing.
- **Actual (f73b7ab):** `maybe_generative_ask` returning None (Insights reached, nothing DMS compiles) fell through to `Cortex.ask`; `map_ask_response_to_envelope` never saw the Insights payload, so served_* were absent with no marker. A null and an absent field were indistinguishable from "no model called".
- **Repro:** `tests/test_served_attr_01.py::test_chat_ask_contract_fallback_after_generate_keeps_served_fields` on f73b7ab (fails: no `served_provider` key).
- **Root-cause class:** copy-through stamped at one return site instead of at the method boundary; absence treated as neutral.
- **Invariant:** silent fallback is a lie, degradation has to show in the output.
- **Fix:** `Executor.live_ask` stamps `served_attribution` (reported/missing/none) on whatever `_live_ask` returns; per-leg served_* in `generate_legs`; "none" only on wire evidence that no model ran.

## Finding 2: an unkeyed DMS reached Cortex with the published key (dms#273)

- **Expected:** no key configured means no Cortex call.
- **Actual:** `cortex_api_key` defaulted to `dms-demo-viewer-key`; `cortex_read` fell back to it; `compute_query(dms_query=True)` and `compute_insights(api_key=None)` still posted generate.
- **Repro:** `tests/test_key_01.py` on 42d8c62 (19 fail).
- **Root-cause class:** a convenience default equal to a public credential; a test-only escape hatch (`missing_none=False`) kept in product code.
- **Invariant:** never cut on trust-boundary validation; a control that blocks legitimate work is a failure (positive control `test_live_mode_with_key_starts_and_forwards_it`).

## R4 held: dms#261 and dms#258 already fixed

- #261: 14c02fe (PR #266) on base and main. Neutralizing `currency_mismatch_reason` turns 15 tests red (10 `*/revenue_usd/*` pins).
- #258: b10a5d2 (PR #230) on base and main. Neutralizing `violations_cited_by_sql` turns 7 red (6 SQL pins + the A2-02 test).
