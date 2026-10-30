---
keywords: [unseal, envelope, E9, E12, verify_demo_live, 31/31, verify_l2_vs_l1, oracle-only, categoty, L2_VALIDATED, spend-as-revenue, OK, coverage]
main_idea: Vault unsealed. 31/31 and oracle-only exit 0. Seven live Chat asks: 3 L0 certified 0 WRONG, 4 honest ABSTAIN (about / strong / OK / categoty). verify_l2_vs_l1 exit 1 on categoty. Harness scalar "total spend" is L2_VALIDATED revenue_myr 80.8M (WRONG-SEMANTIC; envelope E9 still green because SQL ran). Not 100% coverage. R-0010 bound is not zero.
---

# Unseal accuracy scale -- 2026-09-11

PREFLIGHT: HIT `2026-09-11_116-closed-8090-live.md`, `2026-09-10_l1l2-hardcore-live.md`, `2026-09-10_categoty-incomplete.md`, `2026-09-10_prove-23-30-pack-writer.md`, `2026-09-08_bronze-unique-encoding.md`.

No product patch. Did not start Docker. Did not kill :8090/:8010/:5000. Did not start a second OpenVault.

## Stack

- GET `:5000/api/vault/status` 200 `sealed=false`
- GET `:8090/health` 200 `ask_mode=live` `demo_fallback=false` cortex.ok jwks `key_count=4`
- GET `:8010/health` 200 `pack=dms`
- GET `:8010/health/features` `engine_version=2.5.0` extras agentic/rag/full (no `DMS_L2_ENABLED` key in the JSON)
- `DMS_DEMO_FALLBACK=0`

Logs: `D:\DMS\.tmp\verify_demo_live_unseal.txt`, `verify_l2_vs_l1_unseal.txt`, `oracle_only_unseal.txt`, `unseal_scale_asks.json`, `unseal_scale_total_spend.json`.

## Harness scores

| Harness | Exit | Stamp |
|---|---|---|
| `scripts/verify_demo_live.py` | 0 | **31/31** (~28s). Finance spend-by-country L0. Stock-by-category L0 in Finance and Ops. Scalar `What is our total spend?` counted PASS as L2_VALIDATED `revenue_myr=80787598.3` (see WRONG-SEMANTIC below). `add 2000` follow-up honest ABSTAIN. |
| `scripts/verify_l2_vs_l1.py` | 1 | L0 top5 + spend-country pass. `categoty_top3` ABSTAIN (must_not_abstain). hazmat honest ABSTAIN. top11 `L1_GOVERNED_METRIC`. |
| `scripts/score_answers.py --docs tests/fixtures/hostile_score --oracle-only` | 0 | Oracles recomputed. Merge-trap distinct. F32 Sales vs Wide_Fill distinct. Blank rows do not move the oracle. Does not score the live ask path. |

R-0010: 31/31 is not a zero bound. The scalar spend check is compute-or-abstain, not spend!=revenue.

## Live `POST /v1/chat/ask` (Finance, `ses_scale`, timeout 180)

Empty `contributing_sources` on certified SQL is valid. All seven envelopes passed `assert_envelope_valid`. No green badge on abstention prose. No uncited figure without executed query.

| Question | status | badge | abstained | one-line result | WRONG? |
|---|---|---|---|---|---|
| What is this database about? | coverage-miss | ABSTAIN | true | L2 `NO_CANDIDATE`; no number | no |
| Top 3 SKUs by quantity sold | answered | L0_CERTIFIED | false | VQ `outbound` kg: ALPHA 2700 / BETA 950 / EPSILON 900 | no |
| What is our total spend by supplier country? | answered | L0_CERTIFIED | false | Cortex SQL 4 countries; MY 448.8M / TH 74.7M / CN 60.0M / SG 59.5M | no |
| What is total stock value by category? | answered | L0_CERTIFIED | false | Cortex SQL 10 categories (CLEANING / CHEMICALS / PPE / ...) | no |
| Show top 3 categoty sales | coverage-miss | ABSTAIN | true | unknown-noun `categoty`; no SQL | no |
| Is this database strong? | coverage-miss | ABSTAIN | true | L2 `NO_CANDIDATE`; no strength score | no |
| What does OK mean in this data? | coverage-miss | ABSTAIN | true | L2 `NO_CANDIDATE`; no glossary figure | no |

Founder goal (Chat honestly answers about / strong / OK): **not met**. Precision held (those three abstained). Coverage did not.

Two lakes still split (HIT prove-23-30): quantity VQ is the thin demo `transactions` (`SKU-ALPHA`). Spend/stock L0 is the rich Cortex warehouse (`SKU-00173` on the L2 harness top5). One CEO conversation can look like one database and is not.

## Related live ask (not in the seven) -- STOP, no patch

`What is our total spend?` (harness `SCALAR_Q`, session `ses_scale_spend`):

- HTTP 200 `badge=L2_VALIDATED` `abstained=false` `route=sql` `demo_fallback_used=false`
- `assumptions`: `query skill match score=1.000 -> revenue_total`
- SQL: `SUM(quantity_kg * unit_cost_myr) ... FROM transactions WHERE txn_type = 'OUT'` aliased `revenue_myr`
- `values`: `revenue_myr=80787598.3` `audit_id=e02003a3-9fd3-4f44-b9cd-7c0d04dcc4b1`
- `assert_envelope_valid` **passes** (E9: query executed, figure is in values). E12 does not fire (scalar SQL for a one-number ask).

This is WRONG-SEMANTIC: spend bound to `revenue_total` / outbound revenue. Certified spend-by-country is `inventory JOIN suppliers` (MY alone 448.8M). A SQL-only gate would stamp this green. The customer text is a spend total; the badge is L2; the number is revenue.

Do not patch this tick (accuracy specialist stop-and-report).

## Envelope fields that fail closed (on the seven)

- E1: abstain badge matches `abstained`
- E2: abstain empty values/sources/token
- E3: answered paths have values + sql_used + audit_id
- E5: badges in allowlist
- E6: demo_fallback false
- E9: no uncited figure without executed query

E12 held on the grouped spend ask (not collapsed to one number). E12 did not catch spend-as-revenue because that SQL is scalar.

## What a SQL-only test would miss

- About / strong / OK: no SQL. A SQL gate would skip. The customer still gets ABSTAIN copy plus certified suggestions, which is honest.
- categoty: no SQL. Misses unknown-noun-before-synonym on this `:8010` (VQ-01 remainder).
- Scalar spend: SQL exists and executes. A SQL gate would PASS and ship 80.8M as spend.

## EPIC remainder (do not close)

- EPIC-017 #33: envelope E1-E9 held on the seven. Cortex#11 free-form half still open. L2 now emits `L2_VALIDATED` for some asks (spend) while hazmat still honest-abstains.
- EPIC-018 #35: oracle-only is exit 0; live coverage is not 100. Do not close.
- EPIC-019 #38 / VQ-01: categoty still ABSTAIN on this Cortex process. Glossary / "OK" / "what is this database about" are not steward assets.
- #108 stays OPEN. Do not recycle `:8010` unless the founder says so.

## Not claimed

100% coverage. Chat can explain the database or OK. `verify_l2_vs_l1` exit 0. categoty L0. Precision-on-answered 100.00% on the seven (none of those seven were confidently wrong). Independent warehouse recompute (would lock the live duckdb writer).
