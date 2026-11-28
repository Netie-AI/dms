# STATUS.md - DMS
**Last updated:** 2026-11-28, DMS-GOV-01 on DMS-CREATE main 8307ba1
**Remote:** https://github.com/Netie-AI/dms
**Tip:** `DMS_GOV_CONSOLE` default off. Only the exact digit 1 turns it on. Off, `/v1/gov` is not mounted and ask, ingest, and connect stay as they were. On, a manager assigns a daily and monthly token or USD budget and ask, create, and connectors. Enforcement is a DMS pre-check plus OpenVault usage readback. OpenVault has no per-user cap write. Question text is manager-only. Uploads are scanned with vendored gitleaks rules and `pii.py`. Stub 52 was not remeasured. Not PASS. `DMS_CREATE_CONTEXT` default off (digit 1 only). On, Create can use your chats, a questionnaire, or a project. The pack is summaries, a schema map, and small results. Off, Create is unchanged. `DMS_CREATE` default off (digit 1 only). Cursor is POST /v1/agents then GET the run. A repair is a follow-up. No per-run token or USD stop. Live cost is on the draft. OpenVault 402 is the spend stop. A named slide range keeps the deck in range. A chart group is not split onto slides that share a title. A cover rule sits under the measured title. A legend sits outside the axis title. `DMS_FDE_KIT` default off. Only the digit 1 turns the field kit on. `list` works while it is off. Not PASS. `DMS_SSO` default off. Only the exact digit 1 turns it on. Off, the API is unchanged. On is login and company scope, not the ask path. Login calls `compliance_gate` and fails closed when Cortex is down. Stub 52 at `2982960`: correct=43 wrong=0 reconfirm=5 ai=0 rule=43. READY-04 flags default off (digit 1 only). Off, health stays ok and `/v1/ready` is absent. On, a synthetic company connects, confirms read-only tables, confirms joins inferred from names or values, and counts a granted table. Health is degraded when the store is memory. Stub `--main` at `ae429f2`, flag off and flag on: correct=43 wrong=0 reconfirm=5 ai=0 rule=43. mode=stub-exec. Armed unmeasured. Not PASS. `DMS_MEASURE_FROM_SCHEMA` default off (`da8b2d9`, rebased onto `a7c0ce4f`). Off, the ask prompt, envelope, and served route stay as they were. On, compile `unknown_measure` or `missing_metric` asks the model for SQL from the granted schema and retrieved examples. `no_path` and `missing_join` propose that SQL on the join confirm card. Join parse uses `SERVING_DIALECT`. A dropped qualifier is one model repair. Stub 52 off and on: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. Rule-served did not grow. `DMS_TIE_PROJECTION` default off (base `f706308`). Off and on, stub 52 is correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. `DMS_DEADLINE_ADAPTIVE` default off (base `774da35a`). Off and on, stub 52 is correct=43 wrong=0 reconfirm=5 ai=0 rule=43. An expired clock does not abstain `serving_deadline_reserve` while a step returns SQL or a gate result. `DMS_REPLY_HYGIENE` default off (`139b010`, base `4716742`). Off, stub 52 is correct=43 wrong=0 reconfirm=5 clarify=0 abstain=0 refusal_ok=4 ai=0 rule=43. On, correct=43 wrong=0 reconfirm=0 clarify=5 abstain=0 refusal_ok=4 ai=0 rule=43. Empty cards ask back. Abstain did not grow. `DMS_COMPILE_HONOUR_CHECK` default off on main `05bcd414`. Off, stub 52 is the same. On, no reason model, so compile is not served: correct=23 wrong=0 reconfirm=25 ai=0 rule=23 L1=23. `DMS_JOIN_DECLARED_FK` and `DMS_FK_ORPHAN_DISCLOSE` default off. Orphan ratio stays 0 until a steward sets it. `DMS_TENANT_PROVISION` default off: the operator CLI refuses and writes nothing until it is on, then it inserts a tenant row and a default pool as steward, a rerun is a no-op, and it creates no key, grant, or credential. A write route does not provision. A missing tenant is HTTP 409 `tenant_not_provisioned`. `DMS_ONTO_SPACE_SCOPE` stays default off: approved items follow the asking Space, plus a steward `tenant_wide` mark. `DMS_SQL_SOURCE_BIND` stays default off. `DMS_ASKBACK_SPECIFIC` default off. Its stub 52 flag off and on is correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. `DMS_SPACE_CATALOG` default off. Missing trust counts say Not measured yet. Ontology tiles only when ok. No Space says Pick a Space. Connect uses `DMS_INSIGHTS_TIMEOUT_S`. Lists past 100 say showing N of M. Flags stay off. Stub `--main` off: correct=43 wrong=0 reconfirm=5 ai=0 rule=43. On `--ai-first`: correct=0 wrong=0 reconfirm=48 ai=0 rule=0. mode=stub-exec. Armed and pack H unmeasured. Not PASS. `DMS_SIGN` default off. Off, `/v1/sign` is not mounted and the audit page adds no sign panel. On, a typed, drawn, or uploaded signature is a pyHanko PAdES with the tenant key from OpenVault, a hash chain on `sign_audit_events` only, and expiring disclose links. The model cannot sign.
## Direction
AI-first text-to-SQL. Plan: `docs/plans/AI_SQL_SHIFT.md`. `understand_question` is the GRAIN-AI call; `reading` stays empty. `DMS_RULES_LEGACY` stays on unless the value is the digit 0. Digit 0 holds L1, compile, and the question-word lanes. `DMS_AI_FLOOR` is default off, digit 1 only. On, fixed lanes are skipped. One `run_floor` call writes SQL with `run_sql` against the executing lake. A miss puts the error in the question, then escalates. The floor uses `ask_lake` and does not seed a custom lake. Generate is capped at 8s. Studio SSE shows the real steps. Stub median is not a live p50. Live 15-60s was not remeasured. `DMS_AI_FIRST` stays default off.
Escalation: `DMS_ASK_LADDER` defaults off. On, a generative miss climbs repair, richer context, plan, then an OpenVault escalate role. `DMS_AI_CLARIFY` defaults off. On, role `reason` asks back with 2 to 4 checked readings. No verdict keeps the old gates. Direct abstain only for ungranted tables, PII, and destructive SQL. DMS does not name a model.
## 52 pack - number of record (STUB)
STUB at `da8b2d9` and `139b010`, `grade52 --main`, base `a7c0ce4f`. In-process replay, not a live model. Label, not a score. Not PASS. stamp_contradiction=0.
| Run | 52 included | paths | metric |
|-----|-------------|-------|--------|
| defaults | correct=43 wrong=0 reconfirm=5 abstain=0 refusal_ok=4 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_TIE_PROJECTION=1 | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_DEADLINE_ADAPTIVE=1 | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_REPLY_HYGIENE=1 | correct=43 wrong=0 reconfirm=0 clarify=5 abstain=0 refusal_ok=4 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_COMPILE_HONOUR_CHECK=1 | correct=23 wrong=0 reconfirm=25 | ai=0 rule=23 L1=23 | ai_share=0/23 wrong=0 |
| DMS_ONTO_SPACE_SCOPE=1 | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_SCALE_MISS=1 | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_ASKBACK_SPECIFIC=1 | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_MEASURE_FROM_SCHEMA=1 | correct=43 wrong=0 reconfirm=5 | ai=0 rule=43 L1=23 compile=20 | ai_share=0/43 wrong=0 |
| DMS_AI_FIRST=1 | correct=0 wrong=0 reconfirm=48 | ai=0 rule=0 | ai_share=0/0 wrong=0 |
| DMS_AI_FLOOR=1 | correct=0 wrong=0 reconfirm=52 | ai=0 rule=0 | ai_share=0/0 wrong=0 stub median_ms=62 |
Flag off and on: refusal_ok=4, refusal_wrong=0, abstain=0, wrong_ub95=6.7%, coverage 43/43. Flag on moves the five empty cards to clarify=5. Pack H and armed grade52 not measured. Not PASS.
Compile honour, flag on: correct=23 wrong=0 reconfirm=25 ai=0 rule=23. Twenty compile qids move to reconfirm.
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
| NL19-COMPILE-WRONG: honour check before a compile serve. Flag off. | #618 |
| BIRD-ASKBACK-GENERIC-01: stop the generic Space ask-back. Flag off. | #620 |
| BIRD-JOIN-GATE-01 / BIRD-FK-ORPHANS-01: declared FK on a key-less child, one join repair, orphan disclose. Flags off. | #625 |
| FDE-ONTO-SPACE-SCOPE-01: approved items stay in their Space. Flag off. | #610 |
| FDE-REASON-LEAK-01: no raw transport text, no dead-end Yes. Flag off. | #617 |
| AI-FLOOR-01: one `run_sql` call on the executing lake. Flag off. | this branch |
| P0 GEN01-WRONG-TO-LADDER-01: an AI-call error must not serve a GEN-01 L2 | #430 |
| CHECKER-FANOUT-01: hold L2 until grant and grain checks pass | #451 (supersedes #434) |
| COMPILE-FALLBACK-KEEP-01 / INTENT-SPEC-01 | #443 (supersedes #433) / #444 (supersedes #409) |
| SAFE-01: one grant function, one parser | #454 |
| WIRE-01a / b / c / d: contract 1.5.0, dms_payload, plan+SQL stamps, AI rung in grade52 | #449 / #452 / #455 / #453 |
| DMS-GOV-01: manager budgets, spend CSV, audit, upload scan, Studio console. Flag off. | this branch |
| SQL-VOTE: equal rows, one call cap. Not PASS. | #463 |
| DR-0006: writer family | #440 |
| ONTO-DERIVE-01 (BANK-02 has merged) | #316 |
| AI-LADDER-PLAN-01 / ASK-CLARIFY-01 | #431 / #419 |
| GRANT-STRUCT-01 / GRANT-KEY-01 / GRANT-UNICODE-01 | #421 / #422 / #438 |
| GOLD-LEAK-01 / serving-lock deadline | #435 / #437 |
