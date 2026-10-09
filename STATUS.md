# STATUS.md - DMS

**Last updated:** 2026-10-09 MYT
**Remote:** https://github.com/Netie-AI/dms
**Main:** `57d85c52`

## Direction

AI-first, DB-GPT/Genie-style text-to-SQL. The model writes the SQL from ontology, schema, and verified-example context, then executes, checks, and retries.

Escalation ladder: plan-then-solve, error-fed self-correct, stronger model via an OpenVault tier, then reconfirm with the closest answerable question and a Yes button.

Direct abstain is only for ungranted tables and destructive SQL. No hand-coded word or phrasing rules.

DMS never holds or routes provider keys. Routing lives in OpenVault.

## 52 pack

Flag-off AI-off run: 23 correct and 0 wrong of 43 answerable, graded with column names ignored. Strictly as graded that was 21 correct plus 2 column-alias matches.

AI answers are 0 until #426 lands. The live-model number is pending.

## Open work

GitHub issues are the source of truth. Short list: [TASK.md](TASK.md).

| Item | PR |
|------|----|
| Serving attach for the AI schema index and extract SQL | [#426](https://github.com/Netie-AI/dms/pull/426) |
| VALUE-HINT-SPAN | [#423](https://github.com/Netie-AI/dms/pull/423) |
| INTENT-SPEC-01 | [#409](https://github.com/Netie-AI/dms/pull/409) |
| ASK-CLARIFY-01 | [#419](https://github.com/Netie-AI/dms/pull/419) |
| ASK-RECONFIRM-01 | [#425](https://github.com/Netie-AI/dms/pull/425) |
| GRANT-STRUCT-01 | [#421](https://github.com/Netie-AI/dms/pull/421) |
| GRANT-KEY-01 | [#422](https://github.com/Netie-AI/dms/pull/422) |
| CONN-POOL-01 | [#420](https://github.com/Netie-AI/dms/pull/420) |
| ONTO-DERIVE-01 after BANK-02 | [#316](https://github.com/Netie-AI/dms/pull/316) after [#342](https://github.com/Netie-AI/dms/pull/342) |
| AI-LADDER-PLAN-01 | no PR |
| NAME-ECHO-01 | no PR |
