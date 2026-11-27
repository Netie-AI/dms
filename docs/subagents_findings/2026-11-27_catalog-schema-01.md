# CATALOG-SCHEMA-01

PREFLIGHT: MISS for a grant-schema catalog. relation_gap is the wrong test for a catalog row: a `bronze.orders` grant also covers bare `orders`, so a main twin would be listed. Match a row only to grants of its own schema. main stays the stored bare name so the demo fingerprint does not move.

`read_catalog` used to filter `table_schema = 'main'`. File Spaces land in `bronze.<table>`. The catalog now reads the schemas the grants name. No schema list is hard-coded. `_src` and `_ingest_id` stay off non-main tables. Qualified reads quote schema and table separately.

A granted join with no stored ontology abstains `unverified_join:no_ontology` only after validate passes, so an ungranted name keeps `sql_relation_not_granted`. Demo SQL is bare or `main.*` and does not fire. One table does not fire. `DMS_SCHEMA_CONTEXT` stays off.

Sibling DUCKDB-PIN-01: DuckDB 1.x leaves `information_schema.is_generated` null, so generated columns over a marked column were served. When the flag is unreported, the CREATE statement is the source. An engine that reports the flag still uses it.

Stub 52 on this head: correct=43 wrong=0 reconfirm=5 clarify=0 ai=0 rule=43. Rule-served did not grow. Not PASS.
