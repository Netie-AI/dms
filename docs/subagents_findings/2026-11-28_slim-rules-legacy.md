# Slim: one switch for the rule serves

PREFLIGHT: PARTIAL. `DMS_RULES_OFF` and the word-rule allowlist were on file. No repo-wide slim finding.

DMS stays a thin harness. The L1 phrase pack and the ontology compile still serve 43 of the 52 on the exec stub (L1=23, compile=20). They are not deleted. `DMS_RULES_LEGACY` defaults on (any token other than the digit `0`, including a typo). Digit `0` holds those serves and the question-word lanes. `DMS_AI_FIRST` is unchanged.

Stub `grade52 --main` on this tree, compared with origin/main `fa0eeef`:

- Default: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20.
- `DMS_RULES_LEGACY=0`: correct=0 wrong=0 reconfirm=34 abstain=10 refusal_ok=8 ai=0 rule=0.

Rule-served dropped to 0 of 52. Nothing was marked wrong. Not PASS. Armed grade52 was not run.

Archived with `git mv` into `attic/`: four stale scripts and 121 pre-November findings. Connectors, grants, governance, proof, ontology, Studio, auth, `SERVING_DIALECT`, `build_abstain`, import contracts, and the OpenVault client stayed. Files open writers are editing were not moved. See `SLIM_INVENTORY.md`.
