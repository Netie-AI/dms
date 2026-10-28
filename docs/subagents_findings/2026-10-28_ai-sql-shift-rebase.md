# ai-sql-shift rebase

PREFLIGHT: MISS. No prior finding named DMS_AI_FIRST or GRAIN-AI.

Keywords: DMS_AI_FIRST, understand_question, GRAIN-AI, grade52, ai_share, VQ-RAG, embedder, demo_gate, pacer, DuckDB 2.0, 279cbd85, f41c0240

## Main idea

Draft #467 rebased onto main `f41c0240`. `DMS_AI_FIRST` stays default off. Off, the ask path keeps VQ-RAG (`embedder=`), the live harness (`demo_gate=`, pacer), and the DuckDB 2.0 stated-value probe. On, a granted L1 phrase and a compile that would have been submitted are prompt examples and are not served. `understand_question` is the call a later GRAIN-AI ticket uses. `reading` stays None. No word list. No model name. Pin `279cbd85` was not edited.

## Counts seen

Suite, `DMS_SKIP_CONTROL_PLANE_TESTS=1 DMS_DEMO_FALLBACK=0`, exit 0:

3565 passed, 41 skipped, 33 xfailed. 1439.33s.

`grade52.py --self-test` printed `self-test ok`. The stub plant line in that command is not a score.

`grade52.py --main` at `36f7bca`, exit 0:

- included: correct=43 wrong=0 reconfirm=5 abstain=0 refusal_ok=3 refusal_wrong=0 empty_gold=0 gold_broken=1
- paths: ai=0 rule=43 L1=23 compile=20 unattributed=0
- metric ai_share=0/43 wrong=0
- wrong_ub95=6.7% coverage=43/43
- split ai=0 rule=43 unattributed=0 abstain=9

`grade52.py --main --ai-first`, exit 0:

- included: correct=0 wrong=0 reconfirm=48 abstain=0 refusal_ok=3 refusal_wrong=0 empty_gold=0 gold_broken=1
- paths: ai=0 rule=0 unattributed=0
- metric ai_share=0/0 wrong=0
- split ai=0 rule=0 unattributed=0 abstain=52, abstain_parts reconfirm=48 refusal_ok=3 gold_broken=1

`0/0` is not a share. Neither run is PASS. n=348 was not run.
