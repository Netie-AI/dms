# FDE upgrade and rollback

The upgrade kit checks a move from the deployed sha to a target sha. It does
not deploy, restart, or migrate a customer database. Alembic runs only when
all of the following are true:

- `upgrade.scratch_url_env` names an environment variable that holds a
  Postgres URL (the URL is never written into the engagement file)
- the host is loopback (`127.0.0.1`, `localhost`, or `::1`)
- the port is not `8090`
- the database name contains `scratch` or `fde_upgrade`
- that database name is not one of the engagement sources

The customer's operator takes the `pg_dump` and the warehouse copy. The kit
only checks that those files exist and are newer than N hours.

`DMS_FDE_KIT` must be the single character `1`. Unset, `true`, `01`, and
` 1 ` exit 2 with `fde_kit_disabled` and write nothing.

No model is named. This kit does not call OpenVault.

## Precheck

```bash
DMS_FDE_KIT=1 python scripts/fde/kits/upgrade.py \
  --engagement acme/ --phase precheck --target <sha>
```

Optional: `--current <sha>` when the deployed sha is not the git checkout.
`--scan-open-prs` reads open pull requests (read-only) and reports revision
number clashes and stacked migrations. `--watch-dir` and `--watch-ref` do
the same for a local tree or a sha already in the clone.

Checks:

| id | What |
| --- | --- |
| K9.01 | Both shas resolve, and the target is a descendant of current |
| K9.02 | Pending alembic revisions between the two trees |
| K9.03 | Each pending revision has a downgrade that is not empty |
| K9.04 | Scratch database: upgrade to the target head, then downgrade back |
| K9.05 | Duplicate revision ids and revision-number clashes inside the target tree |
| K9.06 | `DMS_*` environment reads removed or renamed between the shas |
| K9.07 | `pg_dump` file and warehouse copy exist and are newer than N hours |
| K9.08 | The command list contains no deploy, restart, or customer-database migration |
| K9.09 | The same clash check across `--watch-dir`, `--watch-ref`, and open PRs |

Exit 0 only when every check is PASS. FAIL is 1. A bad flag or a bad
engagement file is 2 and writes nothing. BLOCKED (a sha must be fetched, or
a report is waiting on a person) is 3. A SKIPPED required check is 1, never 0.

A disabled product path is SKIPPED with the reason. It is not PASS.

## Verify

```bash
DMS_FDE_KIT=1 python scripts/fde/kits/upgrade.py \
  --engagement acme/ --phase verify --target <sha>
```

Re-runs inspect (T5, `scripts/fde/kits/inspect.py`) and held-out accuracy
(T4, `scripts/fde/kits/accuracy.py`) when those modules are in the tree. A
missing module is SKIPPED. Held-out WRONG above the last accepted report is
FAIL. Point `upgrade.accepted_report` and `upgrade.heldout_report` at the
report JSON files. Do not cut over while verify is FAIL, BLOCKED, or SKIPPED.

## Engagement keys

```yaml
tenant_id: acme
flags_on: []
sources:
  - kind: postgresql
    host: db.example
    port: 5432
    database: acme
    user: fde_ro
    password_env: ACME_DB_PASSWORD
upgrade:
  scratch_url_env: FDE_UPGRADE_SCRATCH_URL
  backup_dump: backups/acme.dump
  warehouse_copy: backups/acme.duckdb
  backup_max_age_hours: 24
  accepted_report: runs/accepted-accuracy/report.json
  heldout_report: runs/latest-accuracy/report.json
  flag_renames: []
```

Put the password in the environment variable named by `password_env`. A
`password` key in the file is refused.

## Report

`runs/<UTC>-upgrade/report.json` uses schema `netie.fde.report/1`.
`report.md` is rendered from that JSON. Failed checks come first and include
the fix text. Receipts under `receipts/*.json` are copied with secret-shaped
values removed.

## Rollback

The operator does these steps. The kit does not run them. Prove-host
redeploy needs a founder GO.

1. Image pin. Compose builds the API from `apps/api/Dockerfile` in the
   checkout. Check out the previous sha and recreate the api container from
   that checkout. `deploy/helm` and `deploy/presets` are not in this
   repository. Do not point a rollback at those paths. See `docs/DEPLOY.md`.
2. Alembic downgrade. On the customer database, during the founder window,
   run `alembic downgrade <previous_revision>` from that previous sha. The
   precheck scratch round-trip is the evidence that this downgrade returns
   to the previous revision. Take a fresh `pg_dump` first when the backup
   named in the engagement is older than N hours.
3. Warehouse restore. Stop writers. Replace the DuckDB warehouse file with
   the copy named by `upgrade.warehouse_copy`. Use the copy the precheck
   marked PASS.

`--scan-open-prs` may store `refs/fde-upgrade/pr-<number>` in the local
clone. It does not check those refs out in the working tree.
