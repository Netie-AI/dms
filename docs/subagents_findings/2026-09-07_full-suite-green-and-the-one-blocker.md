---
keywords: [suite-green, control-plane, postgres, F-0028, R-0002, sealed-vault, live_ask_failed, SQLSRC-07, dms-157, ordering, DISTINCT]
main_idea: 814 pass, 0 fail. The 31 control_plane errors were only a missing Postgres and all 31 pass against one. The single remaining skip is the live ask test, and the live ask fails for exactly one reason - OpenVault is sealed - with no demo fallback, which is hard rule 11 holding.
---

# The whole suite, and the one thing an agent cannot clear

**Date:** 2026-09-07
**Branch:** `feat/sqlsrc-08-ontology-on-the-route`

PREFLIGHT: HIT on KB **F-0028** (control_plane errors are a missing Postgres, not
a product collapse) and `2026-09-05_openvault-chat-500-decrypt`.

## The count, and what each part of it means

| Slice | Result | What it took |
|---|---|---|
| `pytest tests/ --ignore=tests/control_plane` | **783 passed, 1 skipped, 0 failed** | the two fixes below |
| `pytest tests/control_plane` | **31 passed** | one throwaway Postgres |
| **total** | **814 passed, 1 skipped, 0 failed** | |

Run time went from about 17 minutes to **5:09**, which was not the goal but is
the same defect measured a second way: most of the missing time was a spawned
subprocess re-importing the whole application to hold a file lock.

## The 31 were never failing

F-0028 called this in advance and it held exactly. `tests/control_plane/conftest.py`
fails loudly rather than skipping when Postgres is unreachable (R-0002), and CI
sets `DMS_SKIP_CONTROL_PLANE_TESTS=1` to make the skip a decision someone took.
Nothing was wrong with them:

```
docker run -d --name dms-cp-postgres -e POSTGRES_USER=dms -e POSTGRES_PASSWORD=dms \
  -e POSTGRES_DB=dms -p 5432:5432 postgres:16-alpine
python -m pytest tests/control_plane -q     # 31 passed
```

RLS cross-tenant reads, `ledger_ref` pointer-only, Space-scoped feeds - all green.
The compose file deliberately does not publish 5432 (Caddy is the only public
port), which is why a standalone container is the right way to run these locally
rather than editing the compose.

## The 1 skip is the sealed vault, and nothing else

`test_live_chat_ask_categoty_hits_oracle_envelope` skips unless the DMS API
answers. Started it to find out what is actually behind it:

```
GET  :8090/health -> 200 {"ask_mode":"live","demo_fallback":false,...}
POST :8090/v1/chat/ask -> 503
  {"code":"live_ask_failed",
   "message":"Server error '500 Internal Server Error' for url
              'http://127.0.0.1:5000/keys/intermediate'"}
```

Cortex up on :8010. OpenVault up on :5000 with `sealed: true`. The 500 is the
sealed vault refusing to hand out the intermediate signing key.

**The important half of this is what it did not do.** With `DMS_DEMO_FALLBACK=0`
it returned 503 naming the real upstream failure rather than answering from the
demo seed. That is hard rule 11 and R-0011 holding on the customer path, observed
rather than asserted - and it is the exact failure mode STATUS warns about
("silent fallback that still returns 200 with demo numbers is forbidden").

Only the founder's passphrase clears this. No agent can, and no amount of
re-running changes it.

## The two reds that were fixed, and why neither was a test problem

**`SELECT DISTINCT` has no ordering guarantee.** Measured: four values, ten
different orders in twelve runs. That order reaches the customer through
`BinderResult.values` -> `binding_text()`, so the same question produced
`country IN ('MY', 'SG', 'TH')` on one run and `('SG', 'TH', 'MY')` on the next.
In a product whose premise is a receipt you can re-derive, a predicate that will
not reproduce byte-for-byte is a defect. `ORDER BY 1` at both sites (R-0004):
20 runs, one ordering. The second site, `demo_ask._resolve_exclude_skus`, has a
sharper edge - its `by_norm` map collapses spellings, so `SKU-BETA` and
`sku_beta` both normalise to `skubeta` and the winner used to be whichever the
hash table yielded last.

**A spawned child re-imported the application to hold a file lock.**
`multiprocessing.Process(target=...)` spawns on Windows, so the child re-imports
the module holding the target and with it `dms_api.app`, `dms_executor` and
FastAPI - about 2.3 s for `dms_executor` alone on an idle machine - before
executing one statement. Under a full run's memory pressure it went past the
10 s barrier and the assertion fired with a message about a lock, which is not
where the problem was. Raising the timeout would have hidden it. The child now
gets only duckdb, launched as source through `subprocess`, and signals readiness
on stdout - a signal that cannot outlive the process that sent it.

## Golden rule

> "Flaky" is a description of a symptom, never a diagnosis. Both of these had
> been read as infrastructure noise; one was a customer-visible predicate that
> would not reproduce, and the other was a two-second import tax paid to hold a
> file lock. Before loosening a timeout or an assertion, find out what the test
> was reporting (R-0002).

## Do not

- Do not read 814 as an accuracy claim. It is a suite result. R-0010 forbids any
  rate claim from it.
- Do not leave `dms-cp-postgres` running and assume CI has it. CI sets
  `DMS_SKIP_CONTROL_PLANE_TESTS=1`; the container is a local convenience.
- Do not chase the remaining skip. It is the vault, it is measured, and it is
  the founder's to clear.
