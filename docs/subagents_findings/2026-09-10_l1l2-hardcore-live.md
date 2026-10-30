# L1/L2 hardcore live (8090 recovered)

keywords: L1, L2, verify_l2_vs_l1, L0_CERTIFIED, L1_GOVERNED_METRIC, hazmat, categoty, 503, EPIC-012
main_idea: :8090 recovered. Top 5 L0_CERTIFIED, top 11 L1_GOVERNED_METRIC, hazmat honest ABSTAIN (Cortex L2 flag still off). categoty still ABSTAIN (VQ-01). Official verify_l2_vs_l1 exit 1 on categoty plus a 503 mid-run. Health probe timeout in the script raised 5s to 20s. #116 not COMPLETE.

PREFLIGHT: HIT l1l2-hardcore-blocked + cortex-ungrounded-l1. EPIC-012 CLOSED. Do not kill :8010 to flip DMS_L2_ENABLED.

## Live Chat (demo_fallback false)

| Ask | HTTP | Badge | Notes |
|-----|------|-------|-------|
| Top 5 selling SKUs by revenue | 200 | L0_CERTIFIED | 5 rows, MYR, sql on transactions |
| show top 3 categoty sales | 200 | ABSTAIN | semantic layer does not define categoty |
| top 11 selling SKUs by revenue | 200 | L1_GOVERNED_METRIC | 11 rows |
| hazmat on-time carriers | 200 | ABSTAIN | no governed metric; L2 not on this Cortex process |

Official `scripts/verify_l2_vs_l1.py`: health 200 after timeout bump; L0 top5 pass; categoty fail (must_not_abstain); then 503 on the next ask. Retry top11 200 L1. Not a product patch for 503.

Offline pytest from [DMS envelope L1 pytest](15ad1bfd-5194-4592-8346-8824cb3567bc): envelope 43, bakeoff 3, E9-02 3, extract-object 3, oracle-only exit 0. VQ-01 first run 8/1/1 skip: `badge None` while live lake was locked; isolated rerun passed. See `2026-09-10_dms-l1l2-offline.md`.

## Not claimed

L2_VALIDATED. 31/31. EPIC-020 COMPLETE. categoty L0.