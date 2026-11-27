# STUDIO-FIX-C

Keywords: STUDIO-FIX-C, DMS_DEADLINE_ASKBACK, serving_deadline_exceeded, ask-back, reconfirm, lake interrupt, ASKBACK_RESERVE_S

Main idea: Flag default off. On, generate and retry stop when only the 2s suggestion reserve remains, and the existing reconfirm call may run even if the generate cap is spent. A lake query that already holds a connection still interrupts on the full deadline minus the deliver margin. Flag-off envelope stays ans_serving_deadline_exceeded. No phrase rule. Unit file tests/test_deadline_askback_01.py. Armed grade52 and pack H not run. Not PASS.

PREFLIGHT: HIT on live-harness-01 (DMS_SERVING_DEADLINE_S). The whole-ask clock was already there. This ticket only reserves the tail for the existing suggestion call.
