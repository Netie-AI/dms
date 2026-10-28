# MODIFICATIONS

Vendored from `https://github.com/NVIDIA/auto-ontology` at
`eb8dd0621d07db3033fe3fddc68f62ff72614610` (Apache-2.0).
DMS does not depend on that repository. The pin is `ao_vendor.VENDOR_SHA`.

Not copied: UI (`frontend/`), auth, Helm charts and release workflows,
MCP (`mcp/`), Alembic, the server, the DAL, connectors for engines other
than the caller's DuckDB connection, embeddings, the vector index, and the
`apache-ossie` git dependency.

Unicode dashes and arrows in copied sources were rewritten to ASCII
(`--`, `-`, `->`, `...`). `LICENSE` and `OPEN_SOURCE_NOTICE` are unchanged.

## Copied with that ASCII rewrite, plus the package rename `auto_ontology` -> `ao_vendor`

- `ao_vendor/catalog/constants.py`
- `ao_vendor/catalog/parsers/sqlglot_extractor.py` (no package imports; error strings split so a line stays within 100 characters; the text is the same)
- `ao_vendor/semantic/constants.py` (comment: the DAL module that defined the other SqlAttribute sources was not vendored)
- `ao_vendor/semantic/models.py`
- `ao_vendor/semantic/date_format.py`
- `ao_vendor/semantic/domain.py` (`load_domain_summary` still reads `.semantic_summaries/` when called; compile does not call it)
- `ao_vendor/semantic/prompts/system_prompt.txt` and `system_prompt.txt.license`
- `ao_vendor/utils/sample_values.py`
- `ao_vendor/utils/sql_identifiers.py`

## Copied, then retargeted

- `ao_vendor/semantic/deterministic.py`
- `ao_vendor/semantic/term_extractor.py`
- `ao_vendor/semantic/fk_suggester.py`

`langchain_core.messages` is now `ao_vendor.llm.messages`.
`auto_ontology.utils.llm_invoke` is now `ao_vendor.llm.ov_client`.
One FK-suggester prompt line is wrapped so it fits the repo line length.
The prompt still names the same evidence. No new word list was added.

## Replaced

- `auto_ontology/utils/llm_invoke.py` is not copied. `ao_vendor/llm/ov_client.py` posts to `{OPENVAULT_URL}/v1/chat/completions` with bearer `DMS_OV_SERVICE_TOKEN` or the file `DMS_OV_SERVICE_TOKEN_FILE`. The JSON body is `role=ontology`, `messages`, and `schema` (the pydantic class name). `temperature` and `max_tokens` are accepted by `get_llm_client` and not sent. A body key `model`, `model_id`, `model_name`, `served_model`, `served_provider`, `provider`, or `strict` raises `model_key_refused`. Import builds no client and reads no env.
- `ao_vendor/llm/messages.py` is the `SystemMessage` / `HumanMessage` pair those modules construct.

## Rewritten to drop pandas, the connector registry, and the DAL

- `ao_vendor/catalog/table_filter.py`: same allow/deny rules on lists of dicts. No pandas.
- `ao_vendor/catalog/normalize.py`: same nullable coercion and base-table fill. `table_type` is casefolded (`BASE TABLE` -> `base table`). No pandas and no numpy. `created` is left as text when present.
- `ao_vendor/catalog/extract.py`: reads `information_schema` and `duckdb_constraints()` through the connection passed in. Does not import `duckdb` and does not open one. Coverage check is the same fail-closed rule. No query log is read (`queries` is `[]`).
- `ao_vendor/semantic/visit_enter.py`: `process_table` still runs FK suggestion, then column specs, then term extraction. It returns `CompiledTable` instead of writing the graph. Sampling is `SELECT DISTINCT ... LIMIT 1000` plus a count of distinct values on the caller connection. The upstream low-cardinality probe, JSONB unit walk, embedder, and store writes are not ported. `ponytail:` the sample cap is 1000 and a failed column is skipped.
- `ao_vendor/semantic/semantic_fk.py`: candidate hits are primary-key columns of other tables, not a vector search. `_llm_pick_hit` and the sample-membership SQL are the upstream steps. The probe runs with `conn.execute` when the LLM returns null. Join-default selection stays in memory. Nothing is merged into a store. Embed endpoint and model config are not read.
- `ao_vendor/semantic/bridge_tables.py`: `propose_bridge_attributes` returns proposed SqlAttributes. Empty input does no call. Unparseable SQL is dropped (sqlglot, duckdb dialect) instead of the server validator. The owner term is the first target term. The reranker is not vendored.
- `ao_vendor/compile_run.py`: new driver. Declared FKs are re-parsed with `extract_tables_and_columns` so the sqlglot parser is on the compile path. Status written into the payload is `proposed`. Each term carries `key`: the declared catalog primary key, else `pk_column_names` from the `FkAndPkResult` reading `process_table` already made (no second call). A table with neither is omitted from `terms` and listed in `unkeyed`.
- `ao_vendor/semantic/fk_suggester.py` and `visit_enter.py`: the same reading's `pk_column_names` is returned on `PotentialFkResult` and `CompiledTable`. The UUID foreign-key filter is unchanged.

## DMS entry

- `packages/executor/dms_executor/ao_compile.py` is new, not vendored. `compile_source` imports `ao_vendor` only when `DMS_AO_COMPILE=1`. Off returns `status=off` and does not touch the connection. `OntologyProposal.served` cannot be true.
