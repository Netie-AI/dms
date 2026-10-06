# STUDIO-SELECT-01

Keywords: STUDIO-SELECT-01, studio_selection, DataSelector, selection, schema, ontology joins, SelectionRefused, selection_empty, selection_unknown_table, selection_unknown_column, selection_no_columns, contract bump, additional_properties, extra ignore, 279cbd85, dms-364

## Main idea

Studio picks tables and columns. `POST /v1/chat/ask` takes `selection: [{table, columns}]`. The executor packs the picked columns' names and types and the ontology joins between picked tables into one additive Cortex ask key, `studio_selection`, and narrows the manifest to those tables. No rows. An empty or unknown pick is a named 400 before any lane runs. No cortex-contract bump: Cortex accepts the key and **ignores** it at both main and pin `279cbd85`. The engine does not read it yet. Not COMPLETE. Nothing PASS.

## Contract bump: measured, no bump

- Cortex `main` @ `c7469da4` and pin `279cbd85`: `packages/cortex_contract/answer.py:32` `class AskRequest(BaseModel)` with no `model_config`, so pydantic default `extra="ignore"`. `CortexOS/api/contract_routes.py:238-239` `@router.post("/ask") async def contract_ask(body: AskRequest)` reads only `question`, `session_id` and `space_id` (main also has `scored_pack_id`, contract 1.4.0). An extra key is not a 422. It is dropped silently.
- DMS vendored `contract/openapi-1.2.0.json:300-327` `AskRequest` has no `additionalProperties: false`.
- DMS wire: `cortex_client/client.py:115` dumps the model into the generated `AskRequest.from_dict`, which keeps unknown keys (`generated/models/ask_request.py:83`) and writes them back out (`:46`). A subclass in the executor (`studio_selection.py:25` `SelectionAskRequest`) is enough. `packages/cortex_client/**`, `contract/**` and the pin are untouched.
- So no bump is needed to **send** it. For Cortex to **use** it, Cortex needs an AskRequest field (its own additive minor). That is a Cortex ticket. Route it to prd-agent. Until it lands, the only engine-enforced effect is the narrower manifest.

## Payload

`studio_selection = {tables: [{table, columns: [{name, type}]}], joins: [{name, from_table, from_columns, to_table, to_columns, cardinality}], joins_omitted: [{name, reason}], ontology_verified}`.

- Types come from `information_schema.columns` (`read_selection_schema`). Only granted tables are looked up, so an ungranted table and a missing one refuse the same way (A-0007).
- Joins come from `load_verified_ontology` (demo ontology). A link is listed only when both ends are row-preserving relations over the selected tables and its key columns are ticked. `many_to_many` (`txn_of_lot`) and unticked keys go in `joins_omitted` by name. A GROUP BY view (`product`) is never offered as a physical join. When there is no verified ontology there are no joins and `ontology_verified` is false.
- No-selection callers send the same wire body as before: `{question, session_id, space_id, tenant_id: null}`.
- A selection never takes demo fallback. `DMS_ASK_MODE=demo` returns 400 `selection_needs_live_ask`. `selection` plus `grounded_tables` returns 400 `selection_with_grounded_tables`.

## Gate

`tests/test_studio_select_01.py` (pytest, in CI) and `apps/ui/src/lib/studioSelection.test.ts` (vitest, **not** in CI). Earlier lanes (VQ, pack, generative) can still answer first under the narrowed manifest. `studio_selection` rides only the contract ask. Not COMPLETE. No live ask was run. Nothing PASS.
