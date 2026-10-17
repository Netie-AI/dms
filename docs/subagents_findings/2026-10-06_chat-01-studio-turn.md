# CHAT-01 Studio clarify/confirm/suggest turn

Keywords: CHAT-01, Studio, clarify, confirm, query_plan, needs_clarification, suggestions, dms-369

Main idea: Studio shows a plan / ontology / clarify turn before SQL. Confirm runs the existing ask. An edit re-plans. Follow-ups are contract `suggestions`. Missing plan and clarify-question fields say `not provided by Cortex`. No model routing. Not COMPLETE.

## Fields rendered

- Ontology tables: `locations[].where.table` (else `locations[].id`). Cortex `retrieve_ontology` at pin `279cbd85` `CortexOS/crew/insights.py:429-443` and `:524-530`. DMS copies them in `packages/core/dms_core/studio_chat_turn.py`.
- Ontology joins: `joins[]` keys `id`, `from`, `to`, `from_property`, `to_property` (`insights.py:510-520`).
- Ontology metrics: `metrics[].id` (`insights.py:464-471`).
- Plan: `query_plan` only when it has `measure`. Same nests as `packages/cortex_client/cortex_client/compute.py:170-190` (`query_plan`, `generative.query_plan`, `generative.plan`, `climb`).
- Clarify block: `route == needs_clarification` plus non-empty `answer`. Engine sets that route at `CortexOS/dms/answer_engine.py:49` and the sentence at `:1132-1145`.
- Follow-ups: envelope `suggestions` (`packages/cortex_client/cortex_client/generated/models/answer.py:39-53`).

## Missing

- Contract Answer has no plan and no clarify-question list. Pin `packages/cortex_contract/answer.py:47-63`. Vendored `contract/openapi-1.2.0.json:243-249` is `suggestions` only. Main Answer adds memory and served fields and still has no plan (`answer.py:64-88` on Cortex main).
- `needs_clarification` is a route, not a list (`packages/cortex_client/cortex_client/models.py:83`).
- Ontology GET does not return `query_plan` (`CortexOS/insights/routes.py:224-230`, `insights.py:1393-1408` ask=false). `DMSQueryResponse.query_plan` (`contract/openapi-1.2.0.json:2066-2077`) is on the execute response. This route does not call it.
- No contract change. No abstain-rule change.

## Not this ticket

ManifestMinter, connectors, scorer, oracle packs, prove pins. Nothing PASS.
