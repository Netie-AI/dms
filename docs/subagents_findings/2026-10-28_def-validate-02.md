# DEF-VALIDATE-02

Keywords: DEF-VALIDATE-02, DMS_DEF_VALIDATE, measure_drift, not_in_ontology, derived, count star, sqlglot, reconfirm_disposition, DMS_RECONFIRM_RUN_HELD, yes:definition, 6bbc372e

Main idea: Flag stays default 0. On, COUNT(*) and COUNT/SUM/AVG/MIN/MAX of one approved attribute or key, at a grain reached only through approved links, pass with provenance `derived:<agg>(<table>.<col>)`. Measure drift stays for a redefined measure name, an aggregate over a column outside the ontology, and a nested or mixed-grain aggregate. A granted table or column missing from the approved ontology reconfirms `not_in_ontology` (disposition offer). The envelope `code` is the identifier `not_in_ontology`, not `not_in_ontology:<table>.<col>`, so #477 `park_def_validate_hold` stores the SQL and Yes stamps `yes:definition:not_in_ontology`. `ungranted` stays the grant failure. Rebased onto main `6bbc372e` (#480). Pin `279cbd85` was not edited. More answers are served when the flag is on. The armed 52 run is the judge.

Suite on clean tree `f5a8707`: 3633 passed, 44 skipped, 33 xfailed, exit 0, 1433.83s. `DMS_SKIP_CONTROL_PLANE_TESTS=1`, `DMS_DEMO_FALLBACK=0`. n=348 not run. Not PASS. The commit that records this suite is later than `f5a8707`.
