# 2026-09-03 accuracy hostile coverage

Keywords: hostile-pack, bronze-sheet, value-norm, score_answers, precision, coverage, accuracy-check

Main idea: Uniquely scoped xlsx+sheet asks hit bronze (top-N and exact filters). Two live waves held 100% precision-on-answered and coverage >= 60% (71.43% then 64.29%, serial confirm 71.43%). BETA/KL still abstain; exact SKU-BETA and Kuala Lumpur certify. Grouped Check accuracy no longer treats the first row as a grand total.

Evidence:
- `scripts/score_answers.py --docs tests/fixtures/hostile_score --space cccccccc-cccc-cccc-cccc-cccccccccccc`
- `tests/test_bronze_sheet_ask.py` (26 passed)
- Curated CEO `score_curated.py --live`: 10/14 L0, 0 WRONG
- Browser Ask mode: certified spend + Check accuracy Match on grouped values
- Constructor `--ask` live 401 without CORTEX_API_KEY; `--self-check` PASS
- Do not start EPIC-022 while EPIC-017 (Cortex#11) is open
