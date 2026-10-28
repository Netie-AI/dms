# STATUS.md - DMS

**Last updated:** 2026-10-28, GRAIN-AI-01
**Remote:** https://github.com/Netie-AI/dms
**Tip:** `DMS_GRAIN_AI` default 0. On, grains come from approved items and an OpenVault reading (role `grain`). Split or low confidence is reconfirm. On main `5522d7a`. Suite on `60192e3`: 3589 passed, 41 skipped, 33 xfailed. n=348 not run. Not PASS.

## Direction

AI-first text-to-SQL. Plan: `docs/plans/AI_SQL_SHIFT.md`. `understand_question` is the GRAIN-AI call; `reading` stays empty. `DMS_AI_FIRST` is default off. On, L1 and compile SQL are examples, not serves.

Escalation: plan, self-correct, stronger model via OpenVault, then reconfirm with Yes. Direct abstain only for ungranted tables, PII, and destructive SQL. No phrasing lanes. DMS does not route provider keys.

## 52 pack - number of record (STUB)

STUB at `7392972`, `grade52 --main`. In-process replay, not a live model. Label, not a score. Not PASS. stamp_contradiction=0.

| Run | 52 included | paths | metric |
|-----|-------------|-------|--------|
| flag off | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| flag on | correct=0 wrong=0 reconfirm=48 | ai=0 rule=0 | ai_share=0/0 wrong=0 |

Both: refusal_ok=3, refusal_wrong=0, gold_broken=1. Flag off wrong_ub95=6.7%, coverage 43/43, tries omitted_pin_mismatch=0. Suite 3573 passed, 41 skipped, 33 xfailed. n=348 not run. Armed 52 not re-run.

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
