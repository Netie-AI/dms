# STUDIO-RESULT-01

Keywords: STUDIO-RESULT-01, ResultView, StampsPanel, SimpleChart, abstain, served_attribution, dms-365

## Main idea

Studio renders one DMS ask envelope. SQL is read-only with a copy button. Rows page through the existing table. A date column plus a numeric column is a line; a category column plus a numeric column is a bar; any other shape says `no chartable shape`. Insight text is `text`, split on the existing Insights marker. Stamps are copied off the envelope. A missing key reads `not stamped`. An abstain shows the named reason and does not render rows. No model picker. Chart library is the SVG `SimpleChart` already in the UI. Nothing PASS. Not #364. Not #362.

## Envelope fields read

- `text`, `badge`, `abstained`, `sql_used`, `assumptions`, `rows`: `packages/executor/dms_executor/envelope.py` `build_answer_envelope`
- `abstain_reason`: only `reserved_as_of_abstain` in that file. Other names follow `tests/redteam/rt_grader.py` `abstain_reason` (GEN-01 assumption, demotion note, other assumption, first text line). Display only. Scorer untouched.
- `served_provider`, `served_model`, `served_local`, `learn_enabled`, `learn_source`, `route_store_id`, `served_attribution`, `generate_legs`: `packages/executor/dms_executor/generative_ask.py`
- `model_calls`, `lane`: `Executor.live_ask` in `packages/executor/dms_executor/__init__.py`
- `plan_source`, `plan_origin`: same generative module
- `engine_as_of`, `engine_as_of_after`, `engine_timezone`, `engine_timezone_after`: `stamp_engine_clock` in `packages/executor/dms_executor/demo_warehouse.py`
- Cortex pin: no ask-envelope writer. The panel reads `cortex_sha` only when the object already has it (score-scan name, not this path).
- Contract version: health key `contract` in `apps/api/dms_api/routes/health.py`. Not copied onto the ask envelope, so the row is `not stamped` unless present.
- Timings: no duration key on the ask envelope. The row is `not stamped`.

## Not this ticket

Payload builder (#364), ManifestMinter / OpenVault (#362), prove pins, scorer, oracle packs, contract. `StudioPage` mount is router state `studioEnvelope` only.
