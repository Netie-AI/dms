# BIRD-SERVING-DEADLINE-01

Keywords: BIRD-SERVING-DEADLINE-01, DMS_DEADLINE_ADAPTIVE, serving_deadline_reserve, stage_ms, 429

Main idea: Flag default off. Off, an expired ask clock still abstains serving_deadline_reserve and the envelope has no stage_ms. On, a generator that returns a new SQL candidate or a gate result is not stopped by that clock. The client cancel still raises ServingWaitCancelled. HTTP 429 backs off with jitter and the transport body is not copied into the payload. stage_ms is the ask-clock spans plus total. Stub 52, rebased onto 774da35a, flag off and on: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. The word-rule allowlist line for question_with_examples moved from 1087 to 1135. No new site. Armed grade52 and the BIRD jsonl were not on this machine. Not PASS.
