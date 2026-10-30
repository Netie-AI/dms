---
keywords: [unseal, live_ask, VQ-02, EPIC-019, curated, synonyms, seed_curated_vqs, Genie]
main_idea: Vault unsealed. Chat is live. Studio POSTs synonyms. Curated VQs must DESCRIBE this lake (outbound, no country). Live score 7/14 L0, 0 WRONG, 100% precision. #116 still has no SQL host. Cascade stays 0.
---

# Unseal then curated VQs (2026-09-09)

PREFLIGHT: HIT genie-accuracy-bar + live-ask-after-unseal + prd-genie-nearness.

## Proof

- GET `/api/vault/status` sealed=false. Did not auto-unseal. Did not start a second OV. Did not kill :8010.
- Started :8090 from D:\DMS (it was down). `stock_on_hand` -> L0_CERTIFIED 7890 metric_tool.
- Studio register now POSTs `synonyms`. Vitest + `test_vq02` synonym hit green.
- First Cortex-pack SQL seed 503d: this lake is the 15-row demo (`txn_type=outbound`, no `suppliers.country`, no `inventory.category`).
- `scripts/seed_curated_vqs.py` now DESCRIBE-gates and uses lake SQL. `--clear` then 6 assets.
- `score_curated.py --live`: **7/14 L0, 0 WRONG, precision-on-answered 100.00 pct**. 3 expected abstains held. Category/country/categoty abstain (no column).
- Browser Chat: "Top 5 selling SKUs by revenue" certified, 5 rows, assumption "verified question registered in Studio". Synonym "Top 5 SKUs by revenue" also L0 verified_query.

## Tickets

| Issue | This tick |
|---|---|
| #38 | Remainder moved: Studio synonyms shipped; 6 lake-matched VQs live. Not COMPLETE (F83 L0 without Cortex ledger; categoty still needs Cortex pack or a category column). |
| #116 / #108 | No listener on :3306 / :1433 / :5432. Cannot extract. |
| #132 | Flag stays 0. |
| #29 #27 #8 | Not seated. Excel MCP, AirGPT dual-eval, two-minute demo. |

## Do not claim

EPIC-019 COMPLETE, #116 COMPLETE, Genie clone, or 13/13 walkthrough. Cascade stays `DMS_CCA_CASCADE=0`.
