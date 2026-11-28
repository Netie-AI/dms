# BIRD-ASKBACK-GENERIC-01

Keywords: BIRD-ASKBACK-GENERIC-01, DMS_ASKBACK_SPECIFIC, scale_miss, _FILL, role reason, validated_sql_reason, yes:reconfirm

PREFLIGHT: MISS. No finding named this generic Space filler.

Main idea: Flag default off. Off, the ask-back prompt, the filler text, and the route stay as they were. On, role reason writes readings from this question and the granted schema. A reading is offered only when its SELECT runs on a read-only probe, and the canned "Show one measure for this Space" line is not an offer. No runnable reading is a plain refusal with no Yes token. Yes runs that SQL through validated_sql_reason and stores the asked question. The first SCALE_MISS generate still runs before schema context unless this flag is also on, in which case that prompt includes the granted schema. Stub 52 is measured in the PR. Armed grade52 and the BIRD jsonl were not on this VM. Not PASS.
