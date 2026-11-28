# FDE-AO-COMPILE-01

**Status:** Not PASS. `tests/test_ao_compile_resilient_01.py` is 14 of the 53 passed in that file plus `tests/test_ao_json_01.py`, `tests/test_ao_vendor_01.py`, and `tests/test_ao_ov_auth_01.py`. `tests/test_onto_data_01.py` and `tests/test_grade_ao_01.py`: 14 passed, 1 skipped. mypy on the CI package set: no issues. import-linter: 3 kept. Armed compile and the full suite were not run. Stub grade52 only. Not PASS.

PREFLIGHT: PARTIAL. AO-JSON-01, AO-VENDOR-01, and GRADE-AO-01 are on file. No prior finding for a per-table compile gap.

## What

`DMS_AO_COMPILE_RESILIENT` defaults to off. Only the digit `1` enables it. The flag is read at call time, not at import.

Off, the OpenVault ontology client still uses the fixed 60 second cap, a timeout is still `ao_ov_unreachable`, a 429 is one refusal, and a failed compile still raises. The proposal JSON of a clean compile matches the unset flag byte for byte. The route still returns 500 when compile raises.

On, the caller passes the deadline (`DMS_AO_COMPILE_DEADLINE_S` on the route; unset means no fixed cap). A timeout is `ao_ov_timeout`. A 429 or transient error is retried up to three sends, and `Retry-After` is the wait when present. One validation miss is logged as field paths only and sent back to the model once. A failed table is a gap. The proposal is `partial` when some tables compiled and `refused` only when none did. `compile_ontology_proposal` compiles the Space's granted tables. The route returns 200 with `gaps`, or 400 `space_id_required`, or 503 with a code. It does not return 500.

`DMS_AO_RESPONSE_FORMAT` is documented as the FDE compile setting. Its default stays off.

DMS sends role `ontology`. It does not name a provider or a model.

## 52

Stub `grade52 --main` on this branch after rebase onto origin/main. Not a live model. Not PASS.

| Run | result |
|-----|--------|
| flag off | correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20 |
| `DMS_AO_COMPILE_RESILIENT=1` | correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20 |

`stamp_contradiction=0`. `refusal_wrong=0`. The rule count did not grow. Armed grade52 was not run.

## Ceiling

ponytail: three transport tries, then the table is a gap. A second validation miss is not repaired again. Upgrade path is a caller-supplied attempt budget, not another fixed timeout.
