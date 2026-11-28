# READY-07 sell profile

PREFLIGHT: MISS. No prior finding for a Studio sell profile, the AI floor entry, or big-schema ask text.

## What shipped

`DMS_SELL_PROFILE` is default off and accepts only the digit 1. Health reports it. The API does not import the executor to read it.

When the digit is 1, `Executor._live_ask` returns from `dms_executor.sell_profile.run_sell` after the grant check and before follow-up, verified lookup, pack, and compile. `run_sell` calls `dms_executor.ai_floor.run_floor` when that module imports. Until #657 lands, the same public helpers plan, validate, submit, and ledger the model SQL. Schema text calls `dms_executor.big_schema.schema_for_ask` when that module imports, then `relevant_schema` or `pick_schema`, then the catalog reader. Those modules are not edited.

A payload `clarify` block with a question and options or readings becomes the card. It does not serve a figure. Empty SQL repairs, then escalates with `route_hint=escalate`. Ungranted SQL and a non-read-only statement abstain on the first call.

The Studio card shows the answer, a chart from the model spec when the columns exist, and buttons for SQL, a row-capped table, and the checks. Steps `understand`, `find`, `write`, `run`, `check`, and `answer` stream on the existing ask SSE. The open step breathes. Reduced motion disables the animation.

## Root cause

Flag-on `/ask` depended on stub `query_sql`:

- `packages/executor/dms_executor/__init__.py` sold the sell-profile `compute` lambda to `_insights_compute_seam`, which calls `cortex.compute_insights`.
- `packages/executor/dms_executor/sell_profile.py` `_harness` took SQL only from `query_sql_from_payload` (`packages/executor/dms_executor/generative_ask.py`).
- `scripts/verify/grade52.py` `_Stub.compute_insights` returns the recorded insights object. Those rows have no `query_sql`, so every question was `no_sql` and then reconfirm.

That replay serves nothing, so it cannot show the AI path.

## Provider loop

`provider_turn` posts to OpenVault `/v1/chat/completions` (role `sql`, no model id, `route_hint` only when the floor escalates). SQL is the `execute_sql` tool argument. A bare `query_sql` field is ignored. `run_sell` still calls `run_floor` when that module imports, and passes this provider turn as `compute`, so the floor executes that SQL on the warehouse. No `ov_` key and no fixture returns no payload.

`DMS_SELL_PROVIDER_FIXTURE` is the recorded-response swap for CI. `tests/test_sell_profile_provider_01.py` answers the three demo-warehouse questions. The rows equal a second execute of `sql_used` on the demo warehouse. A poisoned insights `query_sql` and a poisoned `/ask` are not used. `serve_path` is `ai`.

## Stub 52

`grade52 --main` at `f7f3dac97116fd6318708a569cb49a76595c5a89`. On, there is no provider fixture and no OpenVault key, so the stub still does not serve.

| Profile | correct | wrong | reconfirm | ai | rule |
|---------|---------|-------|-----------|----|------|
| off | 43 | 0 | 5 | 0 | 43 |
| on | 0 | 0 | 52 | 0 | 0 |

Off also has L1=23 compile=20 clarify=0 abstain=0 refusal_ok=4. On has no served path, so ai and rule are both 0. The recorded insights have no `query_sql`, so the profile abstains and the existing reconfirm card is the ask-back. The four flag-off refusals are in that reconfirm count. Nothing was served. WRONG is 0. Not PASS. Armed grade52 was not run.
