# L1/L2 hardcore this tick -- live Chat blocked

keywords: L1, L2, verify_l2_vs_l1, duckdb, duckdb-dll, wedged-8090, session_unbound, EPIC-012
main_idea: Live Chat L1/L2 cannot run while :8090 /health times out. Cortex :8010 is 200 but /v1/contract/ask needs a DMS-bound session (409 session_unbound). Envelope pytest could not even collect: duckdb DLL Insufficient system resources. Duckdb-free L2 bakeoff 3/3. Do not kill :8090/:8010. #116 not COMPLETE.

PREFLIGHT: HIT prd-cortex-genie-l1l2 + cortex-ungrounded-l1. EPIC-012 stays CLOSED.

## Ran

- Cortex :8010 /health 200 pack=dms engine 2.5.0 extras agentic/rag/full. Features do not expose DMS_L2_ENABLED. L2 in answer_engine is off unless that process env is 1.
- POST /v1/contract/ask session_id=demo -> 409 session_unbound for Top 5, top 11, hazmat. Engine ask is not a bypass of DMS mint.
- DMS :8090 /health still 000. verify_l2_vs_l1.py not run (needs :8090).
- pytest tests/invariants/test_envelope.py collection ERROR: ImportError _duckdb DLL load failed Insufficient system resources. Not a product fail.
- tests/test_bakeoff_l2_models.py 3 passed (F46: do not promote on badge/latency).
- [DMS envelope L1 L2 pytest](2a61648f-f6ba-4239-99d6-5973a6ec45bb), [Run Cortex L1 L2 tests](51d351d3-4605-4a01-9548-1d3d2b3bee01), [Map Cortex L1 L2](91ec31a8-1990-4756-b664-2791d7dd84e3) all relaunch-failed. No Cortex finding file written. Did not re-run Cortex pytest (same duckdb DLL class).

## Map (parent, no recycle)

- Chat path: DMS `live_ask` -> VQ / metric_tool / bronze_sheet, then `_cortex.ask` (HTTP). Object-ground `objects.*` stays tools-only.
- Badges: L1 = Cortex `governed_metric`/`catalog` -> `L1_GOVERNED_METRIC`. L2 = `generated`/`session`/`query_skill` -> `L2_VALIDATED` in `CortexOS/api/contract_routes.py`.
- `DMS_L2_ENABLED` is read in the **Cortex** process (`CortexOS/dms/l2_generation.py` `attempt_l2`). Default off. DMS `-EnableL2` on :8090 does nothing if :8010 env is unset. Do not restart :8010 to flip it.
- Live probes without recycle: GET `:8010/health`, `/health/features`. POST `/v1/contract/ask` needs a bound session (409 without DMS mint). `verify_l2_vs_l1.py` needs :8090.
- Chat L2 tests are not `tests/dms/test_l2_pipelines.py` (lakehouse pipelines). Engine files: `tests/dms/test_q2_answer_engine.py`, `tests/dms/test_c7_full_generation.py`.
- Cortex#11 F40 engine half still OPEN per STATUS. Free-form is not a measurement until that closes.

## Not claimed

Live L0/L1/L2 badges. 31/31. EPIC-020 COMPLETE. L2_VALIDATED on this Cortex process.