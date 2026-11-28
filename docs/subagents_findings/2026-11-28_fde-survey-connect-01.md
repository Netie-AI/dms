# FDE-SURVEY-CONNECT-01

Keywords: FDE-SURVEY-CONNECT-01, DMS_FDE_KIT, survey, connect, read-only, max_rows, support matrix

`scripts/fde/kits/survey.py` and `scripts/fde/kits/connect.py` write a `netie.fde.report/1` report. `DMS_FDE_KIT` must be the digit `1`. Off, both commands exit 2 and write nothing. A product route that is off is SKIPPED, including `DMS_CONNECT_API`. No check defaults to PASS.

The engine table in `docs/fde/RUNBOOK.md` is generated from the driver modules. Snowflake, BigQuery, Databricks, and Redshift stay absent. A login with INSERT is a high FAIL and the statement log has no write. A table over `DEFAULT_MAX_ROWS` is a critical FAIL with the cap fix. A wrong password is `Validate login: FAIL` with code `auth` and no password or driver text.

Live on this VM: scratch PostgreSQL 16 and MariaDB 10.11, the five `tests/fde_scenarios` companies on both plus SQLite, a 600000-row table, and an INSERT login. Row counts stayed 600000. Pharmacy `fde_pharma_20261128`, a BIRD SQLite file, and SQL Server were not on the machine. The connect HTTP route was SKIPPED because no DMS API was started. Not PASS.

Not a word rule. No ask-path change. Stub 52 was not remeasured.
