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

    rows = _rows(con, TABLES_SQL, ())
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
    return keys_from_rows(_rows(con, pk_sql, ()), _rows(con, fk_sql, ()), _SKIP)


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
    try:
        return _scalar(con, "SELECT current_setting('sql_compatibility')")
    except Exception as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate == "42704" or "undefined" in type(exc).__name__.lower():
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


def _scalar(con: Any, sql: str) -> str:
    cur = con.cursor()
    try:
        cur.execute(sql)
        row = cur.fetchone()
    finally:
        cur.close()
    if not row or row[0] is None:
        return ""
    return str(row[0])


def _rows(con: Any, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    cur = con.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return [tuple(row) for row in cur.fetchall()]
    finally:
        cur.close()

