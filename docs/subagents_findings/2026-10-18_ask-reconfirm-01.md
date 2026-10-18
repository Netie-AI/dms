# ASK-RECONFIRM-01

Keywords: reconfirm, abstain, ungranted, PII, destructive SQL, grade52, stub-exec, suggested_question, pipeline-failure

Main idea: an unchecked ask offers reconfirm instead of a bare abstain. Ungranted, PII, and destructive SQL stay a direct abstain. Yes re-runs the suggested question on the normal ladder (grant check, validate_compiled_sql, insights call cap). No returns the sentence `not found in the database` and appends one JSONL pipeline-failure record plus a counter. The suggestion is `complete()` text from the ontology, the granted names, and the failure reason. No template.

stub-exec at f7a8535, not a score, not PASS: correct=43 wrong=0 reconfirm=7 abstain=0 (refusal_ok=0 refusal_wrong=2 empty_gold=0 gold_broken=0, n=52). The stub has no `complete`, so those seven suggestions are empty. The seven ids: ops_spend_boundary, trap_last_month, trap_short_paraphrase, ops_supplier_rank_boundary, trap_delayed_count, trap_stock_by_bin, trap_how_full_synonym.

Do not call `grantable_tables` or open the lake while building the offer. That waits out a serving holder and blows the ask deadline.
