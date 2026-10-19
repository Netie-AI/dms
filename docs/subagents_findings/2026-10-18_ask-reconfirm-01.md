# ASK-RECONFIRM-01

Keywords: reconfirm, abstain, ungranted, PII, destructive SQL, grade52, stub-exec, suggested_question, pipeline-failure, grants fail

Main idea: an unchecked ask offers reconfirm instead of a bare abstain. Ungranted, PII, and destructive SQL stay a direct abstain. Yes re-runs the suggested question on the normal ladder (grant check, validate_compiled_sql, insights call cap). No returns the sentence `not found in the database` and appends one JSONL pipeline-failure record plus a counter. The suggestion is `complete()` text from the ontology, the granted names, and the failure reason. No template.

A pack grant miss keeps the curated sentence (`grants fail`) and stamps the grant code through `build_abstain`. `reconfirm_disposition` reads that code, so `ungranted` and `sql_relation_not_granted` are not offered. A literal `SELECT 1` is `ungranted` (no granted base table) and stays a direct abstain. Destructive SQL is one sqlglot write statement, including a write after prose. The dialect is `SERVING_DIALECT`.

stub-exec after merging main `f8bc4449` (#441 CAST written_names, #450 value index, #458 armed grader), not a score, not PASS: `52: correct=43 wrong=0 reconfirm=5 abstain=0 refusal_ok=3 refusal_wrong=0 empty_gold=0 gold_broken=1 mode=stub-exec`. The grader also prints `header mode=stub` and `split ai=0 rule=43 unattributed=0 abstain=9 n=52`. refusal_ok: `ops_supplier_rank_boundary`, `trap_alerts_ungranted`, `trap_high_risk_pending`. gold_broken: `ops_spend_boundary` (expect abstain, no oracle SQL). reconfirm: `trap_last_month`, `trap_short_paraphrase`, `trap_delayed_count`, `trap_stock_by_bin`, `trap_how_full_synonym`. The stub has no `complete`, so those suggestions are empty.

Do not call `grantable_tables` or open the lake while building the offer. That waits out a serving holder and blows the ask deadline.
