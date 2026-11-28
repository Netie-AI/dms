# GaussDB / openGauss and Oracle connectors

Flags, both default off:

- `DMS_CONNECT_GAUSS=1` accepts kind `gauss` (GaussDB centralized and openGauss).
- `DMS_CONNECT_ORACLE=1` accepts kind `oracle`.
- Only the digit `1` turns a flag on. `true`, `yes`, and `on` leave it off.

Off, both kinds are rejected with the same `bad_request` the connect API already
returns for an unknown kind. The ask prompt, the envelope, and the served route
do not change.

Install the extra for the engine you connect:

- `dms[gauss]` uses psycopg 3, which DMS already depends on. Stock libpq speaks
  MD5 and SCRAM. openGauss defaults to SHA-256, so a SHA-256 server returns
  `auth_method_unsupported` and this doc link, never a stack trace and never the
  password: https://docs.opengauss.org/en/docs/latest/getting_started/python.html
  Fix is the openGauss-built driver (its libpq) or an MD5 user.
- `dms[oracle]` installs `oracledb` and connects in thin mode. It does not load
  an Oracle client.

## What a steward gets

A read-only login, stored as an OpenVault reference (`cred_ref`). The body
rejects a raw secret. Test connection returns a typed code: `auth`, `network`,
`permission`, `tls`, `auth_method_unsupported`, `compat_mode_unsupported`,
`compat_mode_unknown`, `product_not_gauss`, `product_not_oracle`.

Gauss reads `version()` and `sql_compatibility` at test time. PG mode and A mode
use the postgres sqlglot dialect. B mode is refused (`compat_mode_unsupported`)
until a MySQL-dialect path is proven. An unknown mode is refused
(`compat_mode_unknown`). A server that is not GaussDB or openGauss is refused
(`product_not_gauss`). Keys come from `pg_catalog`, the same statements as
FDE-PG-KEYS-01, so a SELECT-only login still sees them.

Oracle uses `ALL_TABLES` plus `ALL_CONSTRAINTS` / `ALL_CONS_COLUMNS`, limited to
schemas this login can see, with Oracle-maintained schemas dropped. The sqlglot
dialect is `oracle`. Read-only is the statement allow-list plus
`SET TRANSACTION READ ONLY` when the server accepts it.

There is no row cap and no statement timeout. Pages are a transport window.
Export streams until the driver is done. Cancel stops the stream and says so.
A missing plan cost warns and waits for `confirm_runaway`.

The Space prompt and the sqlglot gate both receive that dialect: `postgres` for
Gauss, `oracle` for Oracle.

## Live images

The CI job must start openGauss. A pull failure or a server that never
reports running fails the job and prints the container logs with the password
removed. Oracle Free skips, with a named reason, when the image will not pull
or the database never prints that it is ready. A live Oracle probe failure
fails the job and prints the driver error with the password removed. Neither
image is a paid service.

- `opengauss/opengauss:5.0.0` is openGauss, license MulanPSL-2.0.
- `gvenzl/oracle-free:23-slim` packages Oracle Database Free. The image scripts
  are open source. The database is free under the Oracle Free Use Terms, not a
  paid SKU. The job does not pull Oracle's paid editions.

Default ports: Gauss 5432 (pass 8000 when the GaussDB listener is on 8000),
Oracle 1521. `db` is the database name for Gauss and the service name for Oracle.
