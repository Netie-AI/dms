"""Azure SQL and Synapse, read-only, behind DMS_CONNECT_AZURE.

Both speak TDS. pymssql is tried first. pyodbc is the swap when the host
has ODBC Driver 18 and pymssql is not installed. The SQL Server container
in CI is the stand-in for Azure SQL. Encryption stays on. A local container
certificate is trusted only when the caller did not ask for TLS verification.

The statement allow-list is the write refusal. The server also gets a
query timeout and a row cap. There is no partial aggregate: a timeout is
an error, and a cut result says ``truncated``.
"""

from __future__ import annotations

from typing import Any

from dms_executor.drivers import DriverError, flag_on
from dms_executor.drivers.guard import refuse
from dms_executor.drivers.limits import (
    CONNECT_TIMEOUT_S,
    ROW_CAP,
    STATEMENT_TIMEOUT_S,
    result,
)
from dms_executor.drivers.schema_map import compact_schema, prompt_lines

KIND = "azure_sql"
DIALECT = "tsql"
DEFAULT_PORT = 1433
FLAG = "DMS_CONNECT_AZURE"
_SKIP = frozenset(
    {"sys", "information_schema", "guest", "db_owner", "db_accessadmin"}
)
_AUTH = frozenset({18456, 18452})
_PERM = frozenset({229, 262, 916})

TABLES_SQL = (
    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
)
COLUMNS_SQL = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
    "FROM INFORMATION_SCHEMA.COLUMNS "
    "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
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
    tls: bool = False,
) -> Any:
    if not enabled():
        raise DriverError("network")
    try:
        import pymssql
    except ImportError:
        pymssql = None
    try:
        if pymssql is not None:
            return pymssql.connect(
                server=host,
                port=int(port),
                user=user,
                password=password,
                database=database,
                login_timeout=int(timeout),
                timeout=STATEMENT_TIMEOUT_S,
            )
        return _odbc(host, int(port), database, user, password, tls=tls, timeout=int(timeout))
    except DriverError:
        raise
    except Exception as exc:
        raise _mapped(exc) from None


def _odbc(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    *,
    tls: bool,
    timeout: int,
) -> Any:
    try:
        import pyodbc
    except ImportError:
        raise DriverError("network") from None
    trust = "yes" if not tls else "no"
    parts = [
        "DRIVER={ODBC Driver 18 for SQL Server}",
        f"SERVER={host},{int(port)}",
        f"DATABASE={database}",
        f"UID={user}",
        f"PWD={password}",
        "Encrypt=yes",
        f"TrustServerCertificate={trust}",
    ]
    try:
        return pyodbc.connect(";".join(parts), readonly=True, timeout=int(timeout))
    except Exception as exc:
        raise _mapped(exc) from None


def _mapped(exc: BaseException) -> DriverError:
    number = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    if number in _AUTH:
        return DriverError("auth")
    if number in _PERM:
        return DriverError("permission")
    return DriverError("network")


def inspect(con: Any) -> dict[str, str]:
    version = _scalar(con, "SELECT CAST(@@VERSION AS NVARCHAR(200))")
    if not version:
        raise DriverError("network")
    return {"version": version[:200], "mode": "", "product": "azure-sql", "dialect": DIALECT}


def server_version(con: Any) -> str:
    return str(inspect(con).get("version") or "")


def visible_tables(con: Any, _database: str) -> list[str]:
    from dms_core.control_plane.connect_grants import clean_tables

    out: list[str] = []
    for schema, name in _rows(con, TABLES_SQL):
        if str(schema).lower() in _SKIP:
            continue
        try:
            out.extend(clean_tables([f"{schema}.{name}"]))
        except ValueError:
            continue
    return out


def keys_payload(_con: Any) -> dict[str, Any]:
    return {"primary_keys": {}, "foreign_keys": []}


def schema_map(con: Any) -> dict[str, Any]:
    from dms_core.control_plane.connect_grants import clean_tables

    grouped: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for schema, table, column, dtype in _rows(con, COLUMNS_SQL):
        if str(schema).lower() in _SKIP:
            continue
        name = f"{schema}.{table}"
        try:
            clean_tables([name])
        except ValueError:
            continue
        if name not in grouped:
            order.append(name)
            grouped[name] = []
        grouped[name].append({"name": str(column), "type": str(dtype)})
    objects = [{"name": name, "columns": grouped[name]} for name in order]
    return compact_schema(DIALECT, objects)


def prompt(tables: list[str], schema: dict[str, Any] | None = None) -> str:
    return prompt_lines(DIALECT, tables, schema)


def page_sql(statement: str, page_size: int, offset: int) -> str:
    return (
        f"SELECT * FROM ({statement}) AS _dms_cap "
        f"ORDER BY (SELECT NULL) "
        f"OFFSET {int(offset)} ROWS FETCH NEXT {int(page_size) + 1} ROWS ONLY"
    )


def governed_ask(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sql: str,
    grantable: set[str],
    tls: bool = False,
    **_extra: Any,
) -> dict[str, Any]:
    if not enabled():
        raise DriverError("network")
    refuse(KIND, sql, grantable)
    con = connect_login(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        timeout=CONNECT_TIMEOUT_S,
        tls=tls,
    )
    try:
        inspect(con)
        mapped = schema_map(con)
        wrapped = page_sql(_one(sql), ROW_CAP, 0)
        columns, fetched = _fetch(con, wrapped)
    finally:
        _close(con)
    rows = [_pair(columns, row) for row in fetched]
    return result(columns, rows, schema=mapped)


def _fetch(con: Any, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    cur = con.cursor()
    try:
        cur.execute(sql)
        description = cur.description or []
        columns = [str(item[0]) for item in description]
        return columns, [tuple(row) for row in cur.fetchall()]
    except DriverError:
        raise
    except Exception as exc:
        raise _mapped(exc) from None
    finally:
        cur.close()


def _pair(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return {columns[i]: _cell(row[i]) for i in range(min(len(columns), len(row)))}


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _one(sql: str) -> str:
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text


def _scalar(con: Any, sql: str) -> str:
    try:
        columns, rows = _fetch(con, sql)
    except DriverError:
        return ""
    if not rows or not columns:
        return ""
    value = rows[0][0]
    return "" if value is None else str(value)


def _rows(con: Any, sql: str) -> list[tuple[Any, ...]]:
    _columns, rows = _fetch(con, sql)
    return rows


def _close(con: Any) -> None:
    try:
        con.close()
    except Exception:
        return
