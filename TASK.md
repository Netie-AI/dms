# TASK.md

Live open work as of 2026-10-09 MYT. State is [STATUS.md](STATUS.md). GitHub is the source of truth.

- **DROP-ROUTE-FIELDS-01** (P0) Cortex rejected every AI call with 422 because DMS sent model/strict fields. No PR.
- **GEN01-WRONG-TO-LADDER-01** (P0) That miss fell back to the GEN-01 rule builder, which served 3 wrong answers. No PR.
- **#426** Share the serving attach so the AI schema index and extract SQL can run. AI answers stay 0 until this lands. https://github.com/Netie-AI/dms/pull/426
- **#423** VALUE-HINT-SPAN: mask only person-classified columns. https://github.com/Netie-AI/dms/pull/423
- **#409** INTENT-SPEC-01: check writer SQL against a second-route spec. https://github.com/Netie-AI/dms/pull/409
- **#419** ASK-CLARIFY-01: one clarifying question when the ask is ambiguous. https://github.com/Netie-AI/dms/pull/419
- **#425** ASK-RECONFIRM-01: confirm stuck asks, including capacity stops. https://github.com/Netie-AI/dms/pull/425
- **#421** GRANT-STRUCT-01: grant asks from parsed SQL objects. https://github.com/Netie-AI/dms/pull/421
- **#422** GRANT-KEY-01: key grants by source + table. https://github.com/Netie-AI/dms/pull/422
- **#420** CONN-POOL-01: one DuckDB connection per lake. https://github.com/Netie-AI/dms/pull/420
- **#342** BANK-02: auditor export of ask, SQL, tables, and outcome. https://github.com/Netie-AI/dms/pull/342
- **#316** ONTO-DERIVE-01: Space ontology, declared types, safe names. Lands after #342. https://github.com/Netie-AI/dms/pull/316
- **AI-LADDER-PLAN-01** Plan-then-solve rung of the escalation ladder. No PR.
- **NAME-ECHO-01** Column-alias matches on the flag-off grade (the 2 matches). No PR.
