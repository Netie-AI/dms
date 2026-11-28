# FDE-SPACE-GRANT-01

**Status:** Not PASS. `tests/test_sql_source_bind_01.py` passed (6). Related grant files passed (64 with this file). Stub 52 flag off and `DMS_SQL_SOURCE_BIND=1` are the same: correct=43 wrong=0 reconfirm=5 ai=0 rule=43 L1=23 compile=20. Armed grade52 not run. Not PASS.

## What

`POST /v1/studio/sources/sql` with a space id landed tables and derived ontology, and wrote no `dms.data_sources` row. The generative path then saw an empty grant. The Cortex path grounded every bronze table.

`DMS_SQL_SOURCE_BIND` defaults off. On, the land calls `register_source` with the bronze names proposed and not granted, and inserts `dms.data_sources` when `DATABASE_URL` is set. The password is not stored. `GET /v1/spaces/{id}/sources` unions that book when Postgres is down. `POST /v1/studio/sources/{source_id}/grants` is mounted only while the flag is on and calls `grant_connected_tables`. No auto-grant.

`grantable_tables` drops a proposed name until that Space grants it. An empty bound Space abstains `ungranted` on both lanes. A later SQL read of a name still outside the grant abstains (`ungranted` on the contract path, `sql_relation_not_granted` on the generative checker). Neither path grounds that name.

## Not a word rule

The model writes the SQL. The grant set is the table list the steward posted. No question phrase was added.

## Ceiling

ponytail: the grant book is in-memory when Postgres is parked (P-DMS-2). The insert is best-effort and does not fail the land. Upgrade: one Postgres row is the only record, and the memory book is the cache of that row.
