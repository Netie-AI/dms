# UX-CLARIFY-CARD-01

Keywords: UX-CLARIFY-CARD-01, DMS_EARLY_CLARIFY, early_clarify, clarify_ambiguous, clarify_example

Main idea: Flag default off. On, one OpenVault call with role early_clarify runs before the SQL loop. Two to four readings reuse the existing clarify card (reason_code clarify_ambiguous) plus a free-text refine. A pick runs the normal ask. The confirmed SQL is stored as provenance clarify_example and is excluded from replay. A missing or single-reading verdict falls through, so the stub 52 stays correct=43 wrong=0 reconfirm=5 ai=0 rule=43 flag off and on. Unit file tests/test_early_clarify_01.py. Armed grade52 not run. Not PASS.

PREFLIGHT: MISS for this ticket. Related hits: 2026-11-27 studio-fix-c (DMS_DEADLINE_ASKBACK), r1-ui-clarify-01 (DMS_AI_CLARIFY). This step reuses those card and envelope types and runs earlier than both.
