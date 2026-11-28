"""GaussDB and openGauss, read-only, behind DMS_CONNECT_GAUSS.

psycopg 3 is tried first. SHA-256 authentication, which openGauss uses by
default, becomes auth_method_unsupported plus the public driver doc. The
password and the driver text are not returned.

PG and A compatibility modes use the postgres dialect. B mode is refused.
Keys are the pg_catalog statements from FDE-PG-KEYS-01.
"""

from __future__ import annotations

from typing import Any

from dms_executor.drivers import DriverError, flag_on, keys_from_rows

KIND = "gauss"
DIALECT = "postgres"
DEFAULT_PORT = 5432
FLAG = "DMS_CONNECT_GAUSS"
AUTH_DOC = "https://docs.opengauss.org/en/docs/latest/getting_started/python.html"

_ACCEPT_MODES = frozenset({"PG", "POSTGRES", "POSTGRESQL", "A", "ORA", "ORACLE"})
_B_MODES = frozenset({"B", "MYSQL", "M"})
_SKIP = frozenset({"pg_catalog", "information_schema", "pg_toast"})

TABLES_SQL = (
    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
)
_TABLES_PG = (
    "SELECT n.nspname, c.relname FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = 'r' AND has_table_privilege(c.oid, 'SELECT') "
    "ORDER BY n.nspname, c.relname"
)
# No LATERAL, no WITH ORDINALITY, no two-array unnest. openGauss 5.0.0
# rejects those. attnum order is the composite-key ceiling.
_PK_NO_ORDINALITY = (
    "SELECT n.nspname, c.relname, a.attname, a.attnum "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class c ON c.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_attribute a "
    "ON a.attrelid = c.oid AND a.attnum = ANY (con.conkey) "
    "WHERE con.contype = 'p' AND has_table_privilege(c.oid, 'SELECT') "
    "ORDER BY n.nspname, c.relname, a.attnum"
)
_FK_NO_ORDINALITY = (
    "SELECT con.conname, n.nspname, c.relname, a.attname, "
    "pn.nspname, pc.relname, pa.attname, a.attnum "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class c ON c.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_class pc ON pc.oid = con.confrelid "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace "
    "JOIN pg_catalog.pg_attribute a "
    "ON a.attrelid = c.oid AND a.attnum = ANY (con.conkey) "
    "JOIN pg_catalog.pg_attribute pa "
    "ON pa.attrelid = pc.oid AND pa.attnum = ANY (con.confkey) "
    "WHERE con.contype = 'f' "
    "AND has_table_privilege(c.oid, 'SELECT') "
    "AND has_table_privilege(pc.oid, 'SELECT') "
    "ORDER BY con.conname, n.nspname, c.relname, a.attnum"
)


def enabled() -> bool:
    return flag_on(FLAG)


def key_sql() -> tuple[str, str]:
    """The FDE-PG-KEYS-01 statements. One copy, shared with PostgreSQL."""
    from dms_executor.db_connector import _FK_POSTGRES, _PK_POSTGRES

    return _PK_POSTGRES, _FK_POSTGRES


def connect_login(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    timeout: int,
) -> Any:
    from dms_executor.db_connector import SourceConnectionError

    try:
        import psycopg
    except ImportError:
        raise SourceConnectionError(
            "psycopg is not installed; GaussDB / openGauss sources are unavailable"
        ) from None
    try:
        return psycopg.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            dbname=database,
            connect_timeout=int(timeout),
        )
    except Exception as exc:
        if _auth_method_unsupported(exc):
            raise DriverError("auth_method_unsupported", doc=AUTH_DOC) from None
        raise


def open_source(cfg: Any) -> Any:
    from dms_executor.db_connector import SourceConnectionError

    if not enabled():
        raise SourceConnectionError(f"unsupported source kind: {cfg.kind!r}")
    try:
        con = connect_login(
            host=cfg.host,
            port=cfg.resolved_port,
            database=cfg.database,
            user=cfg.user,
            password=cfg.password,
            timeout=int(cfg.connect_timeout),
        )
    except DriverError as exc:
        raise _source_error(cfg, exc) from None
    except SourceConnectionError:
        raise
    except Exception:
        raise SourceConnectionError(f"could not connect to {cfg.describe()}") from None
    try:
        inspect(con)
        lock_read_only(con)
    except DriverError as exc:
        _close(con)
        raise _source_error(cfg, exc) from None
    except Exception:
        _close(con)
        raise SourceConnectionError(f"could not connect to {cfg.describe()}") from None
    return con


def lock_read_only(con: Any) -> None:
    cur = con.cursor()
    try:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        con.commit()
        cur.execute("SET TRANSACTION READ ONLY")
    except DriverError:
        raise
    except Exception:
        raise DriverError("permission") from None
    finally:
        cur.close()


def inspect(con: Any) -> dict[str, str]:
    # The read-only transaction is already open. Roll it back so the
    # setting read is not the statement that sees an aborted transaction.
    # The session stays read-only for the next transaction.
    rollback = getattr(con, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            pass
    version = _scalar(con, "SELECT version()")
    if _product(version) == "":
        raise DriverError("product_not_gauss")
    mode = _compat(con).strip().upper()
    if mode in _B_MODES:
        raise DriverError("compat_mode_unsupported")
    if mode not in _ACCEPT_MODES:
        raise DriverError("compat_mode_unknown")
    return {
        "version": version[:200],
        "mode": mode,
        "product": _product(version),
        "dialect": DIALECT,
    }


def server_version(con: Any) -> str:
    return _scalar(con, "SELECT version()")[:200]


def visible_tables(con: Any, _database: str) -> list[str]:
    from dms_core.control_plane.connect_grants import clean_tables

    try:
        rows = _rows(con, TABLES_SQL, ())
    except Exception:
        # A compatibility mode can reject INFORMATION_SCHEMA. pg_class is the
        # same list. ponytail: one extra catalog read, not a second dialect.
        _quiet_rollback(con)
        rows = _rows(con, _TABLES_PG, ())
    out: list[str] = []
    for schema, name in rows:
        if str(schema).lower() in _SKIP:
            continue
        try:
            out.extend(clean_tables([f"{schema}.{name}"]))
        except ValueError:
            continue
    return out


def keys_payload(con: Any) -> dict[str, Any]:
    pk_sql, fk_sql = key_sql()
    try:
        return keys_from_rows(_rows(con, pk_sql, ()), _rows(con, fk_sql, ()), _SKIP)
    except Exception:
        # openGauss 5 rejects WITH ORDINALITY and unnest() of two arrays.
        # The fallback pairs by attnum. Composite key order can differ from
        # conkey order. Upgrade path is the shared SQL when the server accepts it.
        _quiet_rollback(con)
        return keys_from_rows(
            _rows(con, _PK_NO_ORDINALITY, ()),
            _rows(con, _FK_NO_ORDINALITY, ()),
            _SKIP,
        )


def prompt(tables: list[str]) -> str:
    lines = [f"DIALECT: {DIALECT}", "SCHEMA"]
    lines.extend(f"- {name}" for name in tables)
    return "\n".join(lines)


def explain_sql(statement: str) -> str:
    return "EXPLAIN (FORMAT JSON) " + statement


def page_sql(statement: str, page_size: int, offset: int) -> str:
    return (
        f"SELECT * FROM ({statement}) AS _dms_page "
        f"LIMIT {int(page_size) + 1} OFFSET {int(offset)}"
    )


def _product(version: str) -> str:
    text = version.lower()
    if "opengauss" in text:
        return "opengauss"
    if "gaussdb" in text:
        return "gaussdb"
    return ""


def _compat(con: Any) -> str:
    last: BaseException | None = None
    for sql in (
        "SELECT current_setting('sql_compatibility')",
        "SHOW sql_compatibility",
    ):
        try:
            return _scalar(con, sql)
        except Exception as exc:
            last = exc
            sqlstate = getattr(exc, "sqlstate", None)
            if sqlstate == "42704" or "undefined" in type(exc).__name__.lower():
                continue
            continue
    if last is None:
        return ""
    sqlstate = getattr(last, "sqlstate", None)
    if sqlstate == "42704" or "undefined" in type(last).__name__.lower():
        return ""
    raise DriverError("compat_mode_unknown") from None


def _auth_method_unsupported(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "authentication method" in text and "not supported" in text


def _source_error(cfg: Any, exc: DriverError) -> Any:
    from dms_executor.db_connector import SourceConnectionError

    return SourceConnectionError(
        f"could not connect to {cfg.describe()}",
        code=exc.code,
        doc=exc.doc if exc.code == "auth_method_unsupported" else "",
    )


def _close(con: Any) -> None:
    try:
        con.close()
    except Exception:
        return


def _quiet_rollback(con: Any) -> None:
    rollback = getattr(con, "rollback", None)
    if not callable(rollback):
        return
    try:
        rollback()
    except Exception:
        return


def _text(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def _scalar(con: Any, sql: str) -> str:
    cur = con.cursor()
    try:
        cur.execute(sql)
        row = cur.fetchone()
    finally:
        cur.close()
    if not row or row[0] is None:
        return ""
    return str(_text(row[0]))


def _rows(con: Any, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    cur = con.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return [tuple(_text(cell) for cell in row) for row in cur.fetchall()]
    finally:
        cur.close()

