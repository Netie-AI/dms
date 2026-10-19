# AI SQL shift

Founder direction, 2026-10-19. DMS is a DB-GPT / Cortex Analyst / Genie alternative.
The model writes the SQL from ontology, schema, and retrieved verified
examples. It executes, checks, and retries. The harness does not memorise
the business. WRONG stays 0.

This file is the staged plan. Stage 1 is understanding the person. The
code in this change is the default-off switch that makes a memorised lane
stop being the answer, so that understanding has somewhere to stand.
It does not turn the flag on.

## Where the 52 answers come from today

Stub-exec on main `6daf59e8` (reconfirm, #464): correct=43 wrong=0
reconfirm=5, and among the correct answers ai=0 rule=43 (L1=23,
compile=20). Every correct number is a fixed lane.

### L1, 23

`Executor._live_ask` (`packages/executor/dms_executor/__init__.py`) runs
certified-first. `maybe_pack_ask` (`demo_pack.py`) matches the question
to a pack phrase, checks grants, submits that phrase's SQL through
Cortex, appends the ledger, and returns `L1_GOVERNED_METRIC`.

`route_stamp.stamp_route` writes `served_route=l1_governed_metric` and
`ladder_rung=l1`. `grade52.serve_path` reads the badge and counts L1.
The model is not called. `model_sql` is unused.

A phrase match whose grants, submit, or ledger step fails is a named
abstain and later lanes do not run. That refusal stays.

### Compile, 20

`maybe_generative_ask` (`generative_ask.py`) compiles and submits before
a model SQL answer exists:

1. `rank_window_ask` parses a rank window, calls `Ontology.compile`, and
   submits with `served_route=ladder_rung` (rung `compile`).
2. `_try_multi_grain_envelope` calls `try_compile_multi_grain` and
   submits with `served_route=ontology_compile` and
   `plan_origin=ontology_compile`.
3. A ranked or typed plan falls through to `_compile_maybe_unverified`
   and the same compile submit.

`grade52` counts `ontology_ranking`, `ontology_plan`, and
`ontology_compile` as rule/compile, including when `served_model` is
set. A model name beside a compile stamp is not AI.

### What the AI ladder already has

- Insights generate: `_insights_compute_seam` calls `compute_insights`.
  The body is built in `cortex_client.compute._insights_body`.
  `without_route_fields` strips `model` and `strict`. OpenVault routes
  the provider. DMS does not.
- Schema prompt: `schema_context` is sent only when `DMS_SCHEMA_CONTEXT`
  is on. Pin `279cbd85` does not read that field.
- Model SQL that validates takes `_submit_validated` with the model
  route, and only when the producer named `served_model`.
- `run_model_loop` (`sql_loop.py`), behind `DMS_CLOOP_B` (default off):
  checker, read-only execute, error text back to the model, a call cap.
  Hostile SQL, multi-statement, and ungranted SQL do not retry.
- Reconfirm (`reconfirm.py`, #464): an unchecked ask offers the closest
  question the model writes. Yes runs that question through the normal
  ladder. No says "not found in the database". Ungranted, PII, and
  destructive SQL stay a direct abstain.
- The checker (`validate_compiled_sql`, currency, stated values, space
  join rule) already runs before a serve.

The stub records `insights: {ontology: {metrics: []}}` and an ask body
of `SELECT 1 AS n`. With the flag off, L1 and compile return before
that stub ask is served. That is why ai=0.

## What we adopt

Read 2026-10-19.

| System | Schema linking | Verified SQL | Self-correction | We take | We leave |
| --- | --- | --- | --- | --- | --- |
| DB-GPT | LLM picks tables from schema chunks, optional vector top-k, then the SQL prompt. Chart is the next operator. | Not the product path. | Error-feedback retry is discussed, not the core DAG. | Model picks schema. Chart after rows. Error feedback we already have in `run_model_loop`. | No new phrase router. |
| Cortex Analyst | Semantic view: logical tables, dimensions, facts, metrics, joins, synonyms. | Verified Query Repository. Similar questions retrieve SQL. `verified_at` / `verified_by` are provenance. | Agentic Optimize compares generated SQL to verified SQL and proposes semantic-model edits. It does not serve the verified SQL as the answer for every ask. | Ontology is the semantic view. Examples carry provenance. Optimize-style review is later, human-gated. | No YAML clone. No auto-edit of the ontology. |
| Genie | Knowledge store: descriptions, synonyms, joins, expressions. Instructions are business context. | Example SQL is retrieved. A matching question may run the example directly and be marked Trusted. | Agent mode: subtasks, several queries, a self-correction loop, then a report with a chart. | Examples as context. Multi-step check. A report, not a dump. | Do not execute an example because it matched. Trusted-asset serve is the memorised lane we are leaving. |
| Vanna | DDL, docs, and question-SQL pairs retrieved into the prompt. Question-SQL pairs are the strongest context. | `train(question, sql)` / `add_sql`. `remove_training_data` drops a bad row. | `auto_train` can save a corrected query. | Retrieve question-SQL. Revoke is a first-class act. Save after a check or a Yes. | Do not auto-serve the nearest pair. Do not train on `SELECT *`. |

Sources:

- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst
- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository
- https://docs.databricks.com/aws/en/genie-agents/concepts
- https://docs.databricks.com/aws/en/genie-agents/tune-quality
- https://www.databricks.com/blog/pushing-frontier-data-agents-genie
- https://ask.vanna.ai/docs/
- https://try.vanna.ai/docs/train/
- https://github.com/eosphoros-ai/DB-GPT/blob/main/examples/awel/simple_nl_schema_sql_chart_example.py

## Stage 1 - understand the person

Taste and understanding come before SQL. The person should see that the
product followed their intent, asked when it was unsure, and answered in
plain language. A raw row dump is not an answer.

This stage is default-off (`DMS_AI_FIRST`). What this change ships:

- The flag. Unset, `0`, or any token other than `1` / `true` / `yes` /
  `on` is off. Off, the ask path is unchanged.
- On, a granted L1 phrase and a compile that would have been submitted
  are noted as examples. They are not submitted and not served.
- The examples are appended to the Insights question
  (`verified_examples:` / `example_sql:`). The key
  `verified_examples` is lifted off the catalog and is not an Insights
  body field. Pin `279cbd85` is not given a new field. `model` and
  `strict` stay stripped.
- If the model does not return SQL that passes the checker, the ask
  abstains with "I don't have a checked answer for that yet, so I am
  not stating a figure." The existing reconfirm offer can still attach
  at `POST /v1/chat/ask`. A contract SQL that is ungranted, hostile,
  or otherwise a direct refusal still abstains that way and is not
  served. A contract SQL that would have been the answer is not served.
- `grade52` prints `metric ai_share=<ai>/<correct> wrong=<wrong>`.
  CI runs `python scripts/verify/grade52.py --self-test`.
  `python scripts/verify/grade52.py --main --ai-first` is the stub
  measurement. `--main` alone stays flag-off.

What this stage still owes, specified here, not built:

1. Intent. The model reads the question, the ontology, and the granted
   schema, and states the business question it thinks it heard, in one
   sentence. No word list. No per-phrasing lane.
2. Clarify. When that reading is ambiguous, ask one useful question.
   Reconfirm-with-Yes is the commit path we already have. A clarify is
   the same shape when the ambiguity is in the question rather than in
   a failed compile.
3. Plain language. The card leads with the answer and the unit. The
   number on the card is a value that was executed (envelope E9).
4. The card. One answer, one well-chosen chart when the rows are a
   breakdown (`chart_from_rows` already exists), and a short step list:
   what was understood, which schema and ontology were used, the SQL,
   the check. "See SQL", "see rows", and "see check" are disclosures
   under the card, not the card.
5. Steps are stamped and recheckable. The loop entries in `sql_loop`
   are the seed of that list. The UI renders them as sentences.

### How we measure understanding

WRONG=0 stays the gate. These do not replace it.

| Metric | Definition | Where |
| --- | --- | --- |
| ai_share | Correct answers whose `serve_path` is `ai`, over correct answers. `0/0` is not a share. | `grade52` metric line, this change. |
| wrong | Served answers that disagree with gold. Must stay 0. | `grade52` summary, unchanged. |
| reconfirm acceptance | Yes clicks over Yes+No, among offers that were not a direct abstain. | Defined now. The pending token and `reconfirm_accepted` already exist. A rate is a later counter, not a stub score. |
| clarify usefulness | After a Yes, the next checked answer is correct on gold, or a person marks the question as the one they meant. A Yes that produces a wrong number is a fail even if they clicked. | Defined now. Not a stub score. |
| card readability | The lead states no figure that is absent from `values`. The chart, when present, uses the same rows. The step list names plan, schema, SQL, execute, check without a JSON dump. | Later UI check. E4 and E9 already forbid an unbacked figure. |

The stub with the flag on is allowed to go to ai_share `0/0`. That is
honest: the stub has no model SQL. It is not a pass, and it is not a
reason to serve the example.

## Stage 2 - the ladder is the only serve

Still behind the flag.

- Model SQL is the only confident serve. It must pass the one checker
  (grants, hostile/destructive, currency, stated values, space join
  rule, read-only execute).
- Escalation, in order, each step stamped: plan, then SQL with the
  examples in context, then self-correct with the checker error, then
  a stronger model by asking OpenVault for a higher tier (DMS does not
  name the provider), then reconfirm.
- `DMS_CLOOP_B` is the error-feedback rung we already have. Stage 2
  turns that rung on for the flag, it does not invent a second loop.
- L0 verified queries (a steward registered that exact question) stay
  a serve until the store below replaces them. They are not the 43.

## Stage 3 - save and revoke, per Space

Generated SQL is saved as an example when the checker passes or the
person presses Yes. It is retrieved next time as context. It is never
the answer lane.

Stage 1 keeps the row in memory for one ask. The durable store is this
shape, in schema `dms`, no cross-schema foreign key:

| Field | Meaning |
| --- | --- |
| id | New id. Never reused. |
| space_id | The Space. Retrieve does not cross Spaces. |
| question | The question the SQL answered. |
| sql | The SQL. No row values from the answer. |
| source | `l1_seed`, `compile_seed`, `checker`, or `user_yes`. |
| provenance | ask id, checker version, who confirmed (`checker` or the Yes token). |
| created_at | When it was saved. |
| revoked_at | Null while active. |
| revoked_by | Who revoked it. |

Retrieve: active rows for this Space, capped (stage 1 cap is 4, first
in ask order; the upgrade is similarity once the table exists). Revoked
rows are skipped. The prompt renderer already drops `revoked: true`.

Revoke: set `revoked_at`. The row stays for audit. It cannot be served,
because nothing serves these rows.

Do not save an abstain, a direct refusal, or SQL the checker rejected.

## Stage 4 - flag default on

Only when an armed `grade52` (live model, not the stub) shows wrong=0
and ai_share above the flag-off line of `0/43`, on the same gold, twice.
The stub line is not that proof. Acceptance of reconfirm and clarify
is reported beside it and does not excuse a wrong number.

## Governance that stays code

The harness, and only the harness:

- Grants, PII, destructive SQL. One checker. Direct abstain, no
  reconfirm, no example.
- Every step stamped (`loop`, `served_route`, `ai_first.examples`).
- Excel stays source-only. One ledger, the Cortex append. Five ports.
- No hand-coded word or phrasing rule. No per-phrasing lane.
- No provider key in DMS. Routing stays in OpenVault.
- Pin `279cbd85` constants and `without_route_fields` stay as they are.
