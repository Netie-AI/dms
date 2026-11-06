# FDE landing playbook: one customer SQL warehouse (one page)

How a forward-deployed engineer lands a customer's SQL Server, MySQL or PostgreSQL
warehouse on the governed path on main, and proves every answer against the source.
The script is `scripts/verify/fde_land.py` (FDE-LAND-01). Its test is
`tests/test_fde_land_01.py`.

## Prerequisites

- A running DMS reachable from your machine, with Cortex up (every mutation goes
  through the F5 gate and fails closed without it) and `DMS_DEMO_FALLBACK=0`.
- On the DMS host: the source driver (`pyodbc` with ODBC Driver 18, `pymysql`, or
  `psycopg`) and a network path to the source. DMS pulls rows, it never ATTACHes
  (DR-0005).
- A read-only source login. Pass the password in `FDE_SOURCE_PASSWORD`; it is
  never taken from argv and never printed.
- The DMS actor is a steward (`DMS_ACTOR_ROLE=steward`) if you will approve.
- On your machine: this repo and its Python environment, with the same driver,
  because the oracle queries the source directly.

```
FDE_SOURCE_PASSWORD=... python scripts/verify/fde_land.py --dms-url https://dms.host \
  --kind postgresql --host pg.host --database sales --user reader [--table public.orders]
```

## The steps, and what each refusal means

The walk stops at the first refusal, prints `FAIL <name>` with what to do, and exits 1.
`LANDED` and exit 0 mean every step passed.

| Step | Route | Refusal | Meaning, and what to do |
|------|-------|---------|-------------------------|
| connect | `POST /v1/spaces`, `POST /v1/studio/sources/sql` | `source_unreachable` | DMS could not reach or log in. Check host, port, firewall from the DMS host, the login, the driver. |
| | | `source_table_unknown` | A `--table` is not visible to this login. |
| | | `gate_unavailable` / 403 | The Cortex gate refused or did not answer. Check `CORTEX_URL` and the Cortex key. |
| | | `nothing_landed` | The login sees no base tables. |
| | | `truncated_pull` | The pull hit the row cap: the lake is a partial copy and cannot equal the source. Raise `--max-rows` or name fewer tables. |
| grants | none | prints `N/A on main` | No grant API on main. The Space reads the tables landed into it. |
| ontology | `GET /v1/spaces/{id}/ontology` | `ontology_not_derived` | Derive failed at connect; the reason is printed. Fix it, then `POST .../ontology/derive`. |
| | | `ontology_version_proposed` | The source schema changed since the first connect. The new version is proposed and does not answer. There is no promote route on main. |
| | | `no_verified_object` | Every object failed a key check. Declare or fix the primary key at the source. |
| diff | `POST /v1/ontology/data/compile`, `GET /v1/ontology/data/diff` | `steward_grant_required` | The DMS actor is not a steward. With `DMS_AO_COMPILE` off, compile reports `off` and the step passes with nothing to review. |
| approve | `POST /v1/ontology/data/items/{id}/approve` | `steward_review_pending` | Read the diff receipt, then re-run with `--approve` as the steward. |
| ask | `POST /v1/chat/ask` | `envelope_invalid` | The envelope broke an E-rule (E1-E9). This is a DMS defect: file it with the receipt. |
| | | `demo_fallback` | The answer is demo data. Set `DMS_DEMO_FALLBACK=0`. |
| | | `abstained` | The `gap:` line names why (`unverified_join`, `ontology_unverified`, `no_declared_measure`, ...). |
| | | `rows_mismatch` | A green answer that differs from the source: a wrong number. Stop the landing and file it. |
| | | `figure_not_in_text` | The rows match but the text does not state them. File it. |
| | | `oracle_failed` | The oracle could not run on the source, so the answer is unchecked. |

An unverified link (for example a foreign key with orphan rows) does not stop the walk.
It is named on the ontology receipt with its violation, and no question uses it. The
ask path refuses any join over it.

## The question set

The script asks one row count per verified object and one grouped count per verified
many-to-one link (`--max-groups`, default 1). It builds them from the Space ontology
the asks are served from. For each ask it prints the badge, the rendered text, the
rows, and the oracle rows from the source. They must be equal.

## Not yet on main

- **Grant API.** CONNECT-GRANT-01 (#408) is open; the admin routes are read-only.
- **Studio ontology review.** ONTO-CONFIRM-01 (#283) is open. A changed source schema
  lands as a proposed version with no route to promote it. AO items are approved one
  by one through the API.
- **Planning over the confirmed ontology.** CONNECT-ASK-02 (#284) is open. The Space
  ontology has no measures, so a question that needs one abstains with
  `no_declared_measure`.
- **schema_context** is off by default (F129, `DMS_SCHEMA_CONTEXT`). Even when it is
  on, the catalog lists schema `main` only, not a Space's bronze tables.
- **Governed actions** (write-back) are behind `DMS_ACTIONS` and DR-0008, which is
  still Proposed. Do not offer them at a landing.
