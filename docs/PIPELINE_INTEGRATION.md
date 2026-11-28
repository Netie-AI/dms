# Pipeline integration

DMS can sit as one step in a pipeline that already exists. The Python client
and the CLI do nothing until a process calls them. The server routes below
exist only when `DMS_PIPELINE_HOOKS=1`. Unset, or `0`, those routes are 404
and the ask path is unchanged.

The client never sends a model id or a provider id. Model routing stays in
OpenVault. DMS sends a role string on its own model calls. This package does
not.

## Install

```bash
pip install -e packages/dms_sdk
pip install -e packages/dms_airflow
# optional, for a real Airflow worker
pip install -e "packages/dms_airflow[airflow]"
```

`DMS_API_BASE` is the API origin. `DMS_API_TOKEN` is the bearer when the
deployment requires one. The CLI reads the token from the environment and
does not print it.

## CLI

```bash
dms ask --space S --question Q --json
dms bench run --space S --json
dms import --space S --file target/osi_document.json --dry-run
```

Exit codes are the typed outcome only. A WRONG count does not change the
code. Served is 0, error is 1, clarify is 2, reconfirm is 3, refused is 4.

POST is not retried. A second ask could append the ledger twice. GET proof
retries 502, 503, 504, and transport errors, with backoff.

## SDK

```python
from dms_sdk import DmsClient

client = DmsClient(base_url="https://dms.example.test", token=token)
envelope = client.ask(space_id="S", question="Q")
# envelope["outcome"] and envelope["route"]
proof = client.proof_fetch(space_id="S")
```

`semantic_import(..., dry_run=True)` is the default and does not POST.
`dry_run=False` posts to `/v1/pipeline/semantic/import`, which returns 501
`semantic_import_not_implemented` until FEAT-SEMANTIC-IMPORT-01 is merged.

`benchmark_run` posts to `/v1/pipeline/bench`. With no declared cases the
outcome is `error` and `code` is `benchmark_not_configured`. `wrong` is null.
The route does not report WRONG=0 for a bench that did not run.

## Airflow

Put the base URL on an Airflow connection (`conn_id`, for example
`dms_default`): host, schema `https`, and the bearer in the connection
password. Operators take that id. They do not embed a secret.

```python
from dms_airflow import DmsAskOperator, DmsBenchmarkOperator

ask = DmsAskOperator(
    task_id="dms_ask",
    conn_id="dms_default",
    space_id="S",
    question="Q",
)
bench = DmsBenchmarkOperator(
    task_id="dms_bench",
    conn_id="dms_default",
    space_id="S",
)
ask >> bench
```

`DmsBenchmarkOperator` fails the task when `wrong` is above 0. A missing
count fails too (`benchmark_wrong_untyped`), so an unconfigured bench does
not pass as a clean score.

`DmsSemanticSyncOperator` raises `NotImplementedError` until
FEAT-SEMANTIC-IMPORT-01 is merged. Wire `conn_id` now. Do not call it in a
production DAG yet.

## dbt

dbt 1.12 writes `target/osi_document.json` on parse. The post-step is dry-run
by default. It does not import definitions while the server returns 501.

`dbt_project.yml`:

```yaml
on-run-end:
  - "{{ dms_semantic_sync() }}"
```

`macros/dms_semantic_sync.sql`:

```sql
{% macro dms_semantic_sync() %}
  {{ log("dms import is dry-run by default; the server import is not implemented", info=True) }}
{% endmacro %}
```

Shell step, dry-run (the default recipe):

```bash
dms import --space "$DMS_SPACE" --file target/osi_document.json --dry-run
```

Drop `--dry-run` only after FEAT-SEMANTIC-IMPORT-01 is serving
`POST /v1/pipeline/semantic/import`. Until then that POST is HTTP 501.

## Webhooks

`POST /v1/pipeline/webhooks` registers a URL for one Space. The body carries
`secret_ref`, an OpenVault reference (`service/kid` or a key id). A raw
secret field is rejected. The secret is resolved at delivery with
`GET /api/keys/{key_id}/secret` and is not stored, logged, or returned.

Events: `answer.served`, `clarify.needed`, `benchmark.finished`.

The body is canonical JSON (sorted keys, no extra space). Header
`X-Dms-Signature` is `sha256=` plus HMAC-SHA256 of those bytes. Delivery is
one POST, with no retry.

The payload has ids and the outcome (`space_id`, `event`, `outcome`,
`route`, `audit_id`, `run_id`). It has no row values unless the registration
sets `include_values` true. The stored proof still has no row values.

Flag off, every `/v1/pipeline/*` route is 404.
