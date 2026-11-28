"""Oracle read-only source, behind DMS_CONNECT_ORACLE.

python-oracledb thin mode. No Oracle client library is loaded. Keys come
from ALL_CONSTRAINTS and ALL_CONS_COLUMNS, limited to schemas
ALL_TABLES shows this login. System schemas are dropped.

Read-only is the statement allow-list plus SET TRANSACTION READ ONLY when the
server accepts that statement.
"""

from __future__ import annotations

from typing import Any

from dms_executor.drivers import DriverError, flag_on, keys_from_rows

KIND = "oracle"
DIALECT = "oracle"
DEFAULT_PORT = 1521
FLAG = "DMS_CONNECT_ORACLE"

_SKIP = frozenset(
    {
        "sys",
        "system",
        "xdb",
        "ctxsys",
        "mdsys",
        "olapsys",
        "ordsys",
        "outln",
        "dbsnmp",
        "wmsys",
        "appqossys",
        "gsmadmin_internal",
        "ojvmsys",
        "lbacsys",
        "dvsys",
    }
)
_AUTH = frozenset({"1017", "01017"})
_PERM = frozenset({"1031", "01031"})
_NET = frozenset({"12541", "12514", "12170", "12543", "12560"})

TABLES_SQL = "SELECT OWNER, TABLE_NAME FROM ALL_TABLES ORDER BY OWNER, TABLE_NAME"
PK_SQL = (
    "SELECT cc.OWNER, cc.TABLE_NAME, cc.COLUMN_NAME, cc.POSITION "
    "FROM ALL_CONSTRAINTS c "
    "JOIN ALL_CONS_COLUMNS cc "
    "  ON cc.OWNER = c.OWNER AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME "
    " AND cc.TABLE_NAME = c.TABLE_NAME "
    "WHERE c.CONSTRAINT_TYPE = 'P' "
    "  AND c.OWNER IN (SELECT OWNER FROM ALL_TABLES) "
    "ORDER BY cc.OWNER, cc.TABLE_NAME, cc.POSITION"
)
FK_SQL = (
    "SELECT c.CONSTRAINT_NAME, cc.OWNER, cc.TABLE_NAME, cc.COLUMN_NAME, "
    "       rcc.OWNER, rcc.TABLE_NAME, rcc.COLUMN_NAME, cc.POSITION "
    "FROM ALL_CONSTRAINTS c "
    "JOIN ALL_CONS_COLUMNS cc "
    "  ON cc.OWNER = c.OWNER AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME "
    " AND cc.TABLE_NAME = c.TABLE_NAME "
    "JOIN ALL_CONSTRAINTS rc "
    "  ON rc.OWNER = c.R_OWNER AND rc.CONSTRAINT_NAME = c.R_CONSTRAINT_NAME "
    "JOIN ALL_CONS_COLUMNS rcc "
    "  ON rcc.OWNER = rc.OWNER AND rcc.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
    " AND rcc.POSITION = cc.POSITION "
    "WHERE c.CONSTRAINT_TYPE = 'R' "
    "  AND c.OWNER IN (SELECT OWNER FROM ALL_TABLES) "
    "ORDER BY c.CONSTRAINT_NAME, cc.OWNER, cc.TABLE_NAME, cc.POSITION"
)


def enabled() -> bool:
    return flag_on(FLAG)


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
        import oracledb
    except ImportError:
        raise SourceConnectionError(
            "oracledb is not installed; Oracle sources are unavailable"
        ) from None
    thin = getattr(oracledb, "is_thin_mode", None)
    if callable(thin) and thin() is False:
        raise DriverError("thick_mode_refused")
    try:
        return oracledb.connect(
            user=user,
            password=password,
            host=host,
            port=int(port),
            service_name=database,
            tcp_connect_timeout=int(timeout),
        )
    except DriverError:
        raise
    except Exception as exc:
        mapped = _map_ora(exc)
        if mapped is not None:
            raise mapped from None
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


def lock_read_only(con: Any) -> bool:
    """True when the server accepted a read-only transaction. False when it did not.

    The statement allow-list still refuses writes either way.
    """
    cur = con.cursor()
    try:
        cur.execute("SET TRANSACTION READ ONLY")
    except Exception:
        return False
    finally:
        cur.close()
    return True


def inspect(con: Any) -> dict[str, str]:
    banner = _try_scalar(con, "SELECT banner FROM v$version WHERE ROWNUM = 1")
    if banner:
        if "oracle" not in banner.lower():
            raise DriverError("product_not_oracle")
    else:
        banner = _try_scalar(con, "SELECT SYS_CONTEXT('USERENV','DB_NAME') FROM DUAL")
        if not banner:
            raise DriverError("product_not_oracle")
    return {"version": banner[:200], "mode": "", "product": "oracle", "dialect": DIALECT}


def server_version(con: Any) -> str:
    facts = inspect(con)
    return str(facts.get("version") or "")


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
    return keys_from_rows(_rows(con, PK_SQL, ()), _rows(con, FK_SQL, ()), _SKIP)


def prompt(tables: list[str]) -> str:
    lines = [f"DIALECT: {DIALECT}", "SCHEMA"]
    lines.extend(f"- {name}" for name in tables)
    return "\n".join(lines)


def explain_sql(_statement: str) -> str:
    return ""


def page_sql(statement: str, page_size: int, offset: int) -> str:
    return (
        f"SELECT * FROM ({statement}) _dms_page "
        f"OFFSET {int(offset)} ROWS FETCH NEXT {int(page_size) + 1} ROWS ONLY"
    )


def _map_ora(exc: BaseException) -> DriverError | None:
    code = _ora_digits(exc)
    if code in _AUTH:
        return DriverError("auth")
    if code in _PERM:
        return DriverError("permission")
    if code in _NET:
        return DriverError("network")
    return None


def _ora_digits(exc: BaseException) -> str:
    text = str(exc)
    marker = "ORA-"
    idx = text.find(marker)
    if idx < 0:
        return ""
    digits: list[str] = []
    for ch in text[idx + len(marker) : idx + len(marker) + 6]:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return "".join(digits)


def _source_error(cfg: Any, exc: DriverError) -> Any:
    from dms_executor.db_connector import SourceConnectionError

    return SourceConnectionError(f"could not connect to {cfg.describe()}", code=exc.code, doc="")


def _close(con: Any) -> None:
    try:
        con.close()
    except Exception:
        return


def _try_scalar(con: Any, sql: str) -> str:
    try:
        return _scalar(con, sql)
    except Exception:
        return ""


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

