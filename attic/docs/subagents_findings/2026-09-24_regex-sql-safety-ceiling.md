# Regex SQL safety ceiling (A2-05 #261, A2-06 #262)

Keywords: sql safety, regex, sqlglot, currency gate, grain rule, business key, generative SQL, verifier rounds, EPIC-A2, dms-256, dms-261, dms-262
Main idea: DMS decides whether model-generated SQL is safe with regexes. Eleven adversarial verifier rounds on two tickets each found a SQL shape the regexes read differently from DuckDB. The class fix is a parser (sqlglot, already in Cortex) or moving the checks into Cortex; that is a founder call under hard rule 6.

Expected: once a currency or grain-only rule lands, no confident answer ships whose figure is in the wrong currency or counts a duplicated entity, and no question naming no currency abstains.
Actual: every round closed the reported shapes and the next verifier found new ones. Round 6 (#261): a money threshold in WHERE is never read, so COUNT(*) with a USD threshold on MYR data is certified; 'CNY' (Chinese New Year), 'IRR', 'in pounds' (weight) over-abstain. Round 3 (#262): counting customers through o.customer_id over the verified link returns 4 (truth 3).
Repro: branches a2-05 @ c1c3dba and a2-06 @ 0382388 (local worktrees /tmp/claude-0/wt/); verifier probes /tmp/claude-0/probe_a2-0{5,6}_*.py; gate tests/test_hostile_schema_a2.py.
Root-cause class: a check that parses a language with pattern matching certifies what it cannot read. Blocklisting constructs lost; allowlisting one shape lost too once the verifier moved the figure into WHERE, subqueries, or join keys.
Invariant: "Fix the root-cause class, not the symptom in front of you" and "Prove a gate can fail before trusting it green" (each round proved the previous gate could fail).
Ordering trap (measured): #262 alone raises the gate's confident WRONG 7 -> 10, because un-dropping the ontology releases currency answers the all-or-nothing refusal was masking. #262 must land after #261.
Verified: all counts above re-run by independent verifiers. Assumption: a parser closes the class (not yet built here).
