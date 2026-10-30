---
keywords: [host-test, full-rerun, scale, curated, 32/32, VQ-01, verify_l2_vs_l1, RAM]
main_idea: Host full rerun green on live stack. demo 32/32, curated 12/15 L0 0 WRONG, l2-vs-l1 exit 2, live VQ-01 PASS. Skip-CP pack skipped at 0.55 GB RAM. Live VQ-01 now pins grain not snapshot ranks.
---

# Host full rerun + scale check (2026-09-13)

PREFLIGHT: HIT `2026-09-13_l1l2-categoty-spend.md`, `2026-09-11_dms-skip-cp-categoty.md`. Suite pytest lanes skipped (0.55 GB free). Support probe OK 8.25s: OV unsealed, :8010 pack=dms, :8090 cortex.ok, Control /healthz 200, live ask L0_CERTIFIED. :8011 down (expected).

## Host (same :8090/:8010/:5000, no Docker)

| Gate | Exit | Stamp |
|------|------|-------|
| `verify_l2_vs_l1.py` | 2 | L0/L1 green including categoty. hazmat honest L2 NO_CANDIDATE. top11 L1. |
| `verify_demo_live.py` | 0 | **32/32**. Scalar spend L0 `total_spend_myr=642969499.25`. New check spend != revenue PASS. |
| `score_answers --oracle-only` | 0 | Oracles recomputed. |
| `score_curated.py --live` | 0 | **12/15 L0**, 3 honest ABSTAIN, 0 WRONG, precision 100.00 pct. Was 10/14. |
| live VQ-01 pytest | 0 | `test_live_chat_ask_categoty_hits_oracle_envelope` PASS with `DMS_VQ01_LIVE=1`. |

Curated L0 now includes `trap_categoty` and `cq_spend_total`. Ops spend boundary still ABSTAIN.

## Improve this tick

- Live VQ-01 helper pins DISTINCT sku/category + fan-out ceiling, not ELECTRONICS/CHEMICALS snapshot (that lake drifted to FOOD_DRY/ELECTRONICS/PPE).
- Health probe for skipif 1.5s -> 8s (JWKS nested timeout class).
- Demo harness: `scalar spend is not outbound revenue`.
- Curated pack +1: `What is our total spend?`

## Not run

Full skip-CP pytest (~17-32 min) and Cortex crew pack. RAM 0.55 / 13.67 GB. Would page the live stack.

## Not claimed

EPIC-019 COMPLETE. `verify_l2_vs_l1` exit 0. 100% coverage. Skip-CP 831/0. Cortex#104 hazmat SQL.
