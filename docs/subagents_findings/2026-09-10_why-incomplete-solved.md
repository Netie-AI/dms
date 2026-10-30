---
keywords: [incomplete, SQLSRC-06, dms-116, ERROR-6125, restrict_fk_on_non_standard_key, JWKS, 31/31, categoty, DuckDB-lock, L2]
main_idea: Incomplete was three different gates. MySQL 8.4 ERROR 6125 dropped the lying FK (ops ALTER after restrict_fk OFF). 30/31 was a 2s nested JWKS race. categoty/L2 need a Cortex recycle. This tick: FK named many_to_many fanout 2, 60 vs 90, verify_demo_live 31/31. Do not close. Do not recycle :8010.
---

# Why it was incomplete, and what this tick solved (2026-09-10)

PREFLIGHT: HIT. Reused sqlsrc06-live-verify, mysql84-fk-6125, jwks-health-timeout, categoty-incomplete.

SO / docs: Skeema + MySQL 8.4 restrict_fk_on_non_standard_key default ON -> ERROR 6125. DuckDB one writer process excludes other processes (SO 77364053).

## Three incompletes (do not mix)

1. #116 / EPIC-020 -- MySQL catalog had no FK; verify_demo_live was 30/31.
2. L1/L2 harness -- verify_l2_vs_l1.py exit 1 on categoty must_not_abstain; hazmat cannot stamp L2_VALIDATED on this :8010.
3. Pytest flakes -- live DuckDB exclusive lock. Isolated warehouse passes.

## Solved this tick (no process kill)

- MySQL lying FK: SET SESSION restrict_fk_on_non_standard_key=OFF then ALTER. Receipt names FK_Orders_Customers many_to_many max_fanout=2.
- True SUM 60, JOIN 90.
- Envelope 200 ABSTAIN, assert_envelope_valid pass, demo_fallback_used false.
- verify_demo_live 31/31 (cortex.ok true this tick).

Product disk: JWKS_REFRESH_PROBE_TIMEOUT=8.0 in apps/api/dms_api/routes/health.py. Live :8090 still has the old 2s probe until recycle.

## Not solved (needs founder recycle of :8010)

- show top 3 categoty sales ABSTAIN -- origin/main has cq_top3_category_sales. This process does not.
- Hazmat L2_VALIDATED -- DMS_L2_ENABLED unset.

Do not invent COMPLETE. Do not close #116 from this seat.