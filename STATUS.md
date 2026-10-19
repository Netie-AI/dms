# STATUS.md - DMS

**Last updated:** 2026-10-18 MYT, from live GitHub
**Remote:** https://github.com/Netie-AI/dms
**Tip:** merges main `6daf59e` (#464). SQL-VOTE #463. n=348 not run. Not PASS.

## Direction

AI-first, DB-GPT/Genie-style text-to-SQL. The model writes the SQL from ontology, schema, and verified-example context, then executes, checks, and retries.

Escalation ladder: plan-then-solve, error-fed self-correct, stronger model via an OpenVault tier, then reconfirm with the closest answerable question and a Yes button.

Direct abstain is only for ungranted tables and destructive SQL. No hand-coded word or phrasing rules. DMS never holds or routes provider keys. Routing lives in OpenVault.

## 52 pack - number of record (STUB)

STUB: an in-process TestClient that replays recorded Cortex responses, not a live-model run. Layout copy-only: the shipped image does not copy `tests/`, so the score-pack fixture is absent. Graded by `scripts/verify/grade52.py` (on main via #432).

| Main | 52: correct / wrong / abstain | 45 | Served paths |
|------|------|------|------|
| `57d85c52` (`tests/fixtures/ask_guide/copy_only_57d85c52.jsonl`) | 16 / 0 / 27 | 16 / 0 / 20 | ai=0 rule=16 |
| this branch stub-exec `--main`. Not a score. | 43 / 0 / 0 reconfirm=5 | 36 / 0 / 0 | ai=0 rule=43 |

Copy-only row: refusal_ok=8, refusal_wrong=0, gold_broken=1. stub-exec: refusal_ok=3, refusal_wrong=0, gold_broken=1, wrong_ub95=6.7%, coverage 43/43, split ai=0 rule=43 unattributed=0 abstain=9. Not PASS.

With a model configured (Cortex Check, DMS 57d85c52), Cortex rejected every AI call with 422 because DMS sent `model` and `strict`. DMS fell back to the GEN-01 rule builder, which served 3 wrong answers. #442 (`79b7a274`) fixed the 422: DMS now strips `model` and `strict` from every Cortex Insights POST (`without_route_fields` in `insights.py` and `compute.py`). The OpenVault chat preflight still pins provider and model (`strict_pin.py`). The open P0 is #430: an AI-call error must not serve a GEN-01 L2. The live-model number is pending. The stub does not arm Insights, so its ai=0 says nothing about the AI lane.

## Merged since 2026-10-09

| PR | Squash | What |
|----|--------|------|
| [#420](https://github.com/Netie-AI/dms/pull/420) | `404f3999` | CONN-POOL-01: one DuckDB connection per lake |
| [#432](https://github.com/Netie-AI/dms/pull/432) | `8ff2623a` | GRADER-VALUES-01: grade52 and the copy-only number of record |
| [#423](https://github.com/Netie-AI/dms/pull/423) | `a52ccb22` | VALUE-HINT-SPAN: mask only person-classified columns |
| [#342](https://github.com/Netie-AI/dms/pull/342) | `5b2456fb` | BANK-02: auditor export of ask, SQL, tables and outcome |
| [#445](https://github.com/Netie-AI/dms/pull/445) | `2a78c4bb` | CI: lint-type-test timeout 30 minutes |
| [#442](https://github.com/Netie-AI/dms/pull/442) | `79b7a274` | P0 DROP-ROUTE-FIELDS-01: stop sending `model` and `strict` to Cortex (the 422). Supersedes #429, still open |
| [#447](https://github.com/Netie-AI/dms/pull/447) | `2083e6ac` | Extract and result-hash probe on the pooled serving cursor. Supersedes #426, still open |
| [#448](https://github.com/Netie-AI/dms/pull/448) | `ed7c4011` | EVAL-01: grade52 wrong-rate bound, worst-case repeats, baseline diff |
| [#446](https://github.com/Netie-AI/dms/pull/446) | `3d19dea3` | INGEST-COLNAME-01 (P1 security): a source column name is one quoted identifier |

## Open work

[GitHub issues](https://github.com/Netie-AI/dms/issues) and [open PRs](https://github.com/Netie-AI/dms/pulls) are the source of truth. Never a markdown backlog. P0s first.

| Item | PR |
|------|----|
| P0 GEN01-WRONG-TO-LADDER-01: an AI-call error must not serve a GEN-01 L2 | #430 |
| CHECKER-FANOUT-01: hold L2 until grant and grain checks pass | #451 (supersedes #434) |
| COMPILE-FALLBACK-KEEP-01 / INTENT-SPEC-01 | #443 (supersedes #433) / #444 (supersedes #409) |
| SAFE-01: one grant function, one parser | #454 |
| WIRE-01a / b / c / d: contract 1.5.0, dms_payload, plan+SQL stamps, AI rung in grade52 | #449 / #452 / #455 / #453 |
| SQL-VOTE: equal rows, one call cap. Not PASS. | #463 |
| DR-0006: writer family | #440 |
| ONTO-DERIVE-01 (BANK-02 has merged) | #316 |
| AI-LADDER-PLAN-01 / ASK-CLARIFY-01 | #431 / #419 |
| GRANT-STRUCT-01 / GRANT-KEY-01 / GRANT-UNICODE-01 | #421 / #422 / #438 |
| GOLD-LEAK-01 / serving-lock deadline | #435 / #437 |

Not listed: 15 older open drafts (#424, #408, #398, #397, #391, #379, #378, #376, #375, #344, #343, #339, #336, #331, #329).
