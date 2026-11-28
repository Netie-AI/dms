"""Read-only catalog probe. No statement here writes customer data.

Row estimates come from catalog stats. A missing or zero estimate is confirmed
with ``SELECT COUNT(*)`` because a stale zero is not proof the table is under
the cap. Passwords and driver text are not stored on the result.
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass, field
from typing import Any

from kits._boot import boot

boot()

CAP_FIX = "Raise max_rows for this table or agree a filtered extract before any accuracy claim."
CAP_SOURCE = "packages/executor/dms_executor/db_connector.py:45"
KEY_SOURCE = "packages/executor/dms_executor/db_connector.py"

_WRITE = re.compile(
    r"^\s*(insert|update|delete|merge|create|drop|alter|truncate|grant|revoke|"
    r"copy|analyze|vacuum|call|do)\b",
    re.IGNORECASE,
)
_WRITE_PRIVS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "INDEX",
        "CREATE VIEW",
        "TRIGGER",
        "GRANT OPTION",
        "ALL PRIVILEGES",
        "SUPER",
        "FILE",
        "EXECUTE",
        "CREATE ROUTINE",
        "ALTER ROUTINE",
        "EVENT",
        "CREATE USER",
        "RELOAD",
        "SHUTDOWN",
        "CREATE TABLESPACE",
    }
)

_PG_PRIV = (
    "SELECT "
    "CASE WHEN r.rolsuper OR r.rolcreatedb OR r.rolcreaterole THEN 1 ELSE 0 END, "
    "CASE WHEN EXISTS ("
    "SELECT 1 FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = 'r' "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
    "AND (pg_catalog.pg_get_userbyid(c.relowner) = current_user "
    "OR has_table_privilege(c.oid, 'INSERT') "
    "OR has_table_privilege(c.oid, 'UPDATE') "
    "OR has_table_privilege(c.oid, 'DELETE') "
    "OR has_table_privilege(c.oid, 'TRUNCATE'))"
    ") THEN 1 ELSE 0 END "
    "FROM pg_catalog.pg_roles r WHERE r.rolname = current_user"
)
_PG_STATS = (
    "SELECT n.nspname, c.relname, "
    "CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples END, "
    "pg_catalog.pg_total_relation_size(c.oid) "
    "FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = 'r' "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
    "AND has_table_privilege(c.oid, 'SELECT') "
    "ORDER BY n.nspname, c.relname"
)
_PG_COLS = (
    "SELECT table_schema, table_name, column_name, data_type, "
    "numeric_precision, numeric_scale "
    "FROM information_schema.columns "
    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
)
# information_schema.GRANTEE is quoted ('user'@'host'). CURRENT_USER() is not.
_MY_WHO = "REPLACE(GRANTEE, CHAR(39), '') = CURRENT_USER()"
_MY_PRIV = (
    "SELECT PRIVILEGE_TYPE FROM ("
    "SELECT PRIVILEGE_TYPE FROM INFORMATION_SCHEMA.USER_PRIVILEGES "
    f"WHERE {_MY_WHO} "
    "UNION SELECT PRIVILEGE_TYPE FROM INFORMATION_SCHEMA.SCHEMA_PRIVILEGES "
    f"WHERE {_MY_WHO} AND TABLE_SCHEMA = DATABASE() "
    "UNION SELECT PRIVILEGE_TYPE FROM INFORMATION_SCHEMA.TABLE_PRIVILEGES "
    f"WHERE {_MY_WHO} AND TABLE_SCHEMA = DATABASE()"
    ") g"
)
_MY_STATS = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS, "
    "DATA_LENGTH + IFNULL(INDEX_LENGTH, 0) "
    "FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'"
)
_MY_COLS = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
    "NUMERIC_PRECISION, NUMERIC_SCALE "
    "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s"
)
_SS_PRIV = (
    "SELECT CASE WHEN IS_SRVROLEMEMBER('sysadmin') = 1 "
    "OR IS_MEMBER('db_owner') = 1 OR IS_MEMBER('db_datawriter') = 1 "
    "OR IS_MEMBER('db_ddladmin') = 1 OR IS_MEMBER('db_securityadmin') = 1 "
    "OR IS_MEMBER('db_accessadmin') = 1 THEN 1 ELSE 0 END, "
    "CASE WHEN EXISTS ("
    "SELECT 1 FROM sys.database_permissions p "
    "WHERE p.grantee_principal_id = DATABASE_PRINCIPAL_ID() "
    "AND p.state IN ('G', 'W') "
    "AND p.permission_name IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CONTROL')"
    ") THEN 1 ELSE 0 END"
)
_SS_STATS = (
    "SELECT s.name, t.name, SUM(p.rows), SUM(a.total_pages) * 8192 "
    "FROM sys.tables t "
    "JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
    "JOIN sys.allocation_units a ON a.container_id = p.partition_id "
    "GROUP BY s.name, t.name"
)
_SS_COLS = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
    "NUMERIC_PRECISION, NUMERIC_SCALE FROM INFORMATION_SCHEMA.COLUMNS"
)
_ORA_PRIV = (
    "SELECT COUNT(*) FROM SESSION_PRIVS WHERE PRIVILEGE IN ("
    "'INSERT ANY TABLE', 'UPDATE ANY TABLE', 'DELETE ANY TABLE', "
    "'CREATE ANY TABLE', 'ALTER ANY TABLE', 'DROP ANY TABLE')"
)
_ORA_STATS = "SELECT OWNER, TABLE_NAME, NUM_ROWS, BLOCKS * 8192 FROM ALL_TABLES"
_ORA_COLS = (
    "SELECT OWNER, TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_PRECISION, DATA_SCALE "
    "FROM ALL_TAB_COLUMNS"
)


class WriteRefused(RuntimeError):
    """A statement the kit built would write. It was not sent."""


class Guard:
    """Forward a DB-API connection and refuse a write statement."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.sql: list[str] = []

    def note(self, sql: str) -> None:
        text = str(sql)
        self.sql.append(text)
        if _WRITE.match(text):
            raise WriteRefused()

    def execute(self, sql: str, params: Any = None) -> Any:
        self.note(sql)
        if params:
            return self.raw.execute(sql, params)
        return self.raw.execute(sql)

    def cursor(self) -> GuardCursor:
        return GuardCursor(self)

    def commit(self) -> None:
        commit = getattr(self.raw, "commit", None)
        if callable(commit):
            commit()

    def rollback(self) -> None:
        rollback = getattr(self.raw, "rollback", None)
        if callable(rollback):
            rollback()

    def close(self) -> None:
        close = getattr(self.raw, "close", None)
        if callable(close):
            close()


class GuardCursor:
    def __init__(self, guard: Guard) -> None:
        self.guard = guard
        self.cur: Any = None

    def execute(self, sql: str, params: Any = None) -> Any:
        self.guard.note(sql)
        self.cur = self.guard.raw.cursor()
        if params:
            return self.cur.execute(sql, params)
        return self.cur.execute(sql)

    def fetchall(self) -> list[Any]:
        if self.cur is None:
            return []
        return list(self.cur.fetchall())

    def fetchone(self) -> Any:
        if self.cur is None:
            return None
        return self.cur.fetchone()

    def close(self) -> None:
        if self.cur is not None and hasattr(self.cur, "close"):
            self.cur.close()


@dataclass
class TableStat:
    schema: str
    name: str
    rows: int | None
    nbytes: int | None
    row_source: str


@dataclass
class ColumnStat:
    schema: str
    name: str
    column: str
    data_type: str
    precision: int | None
    scale: int | None


@dataclass
class Facts:
    kind: str = ""
    driver_kind: str = ""
    accepted: bool = False
    skip_reason: str = ""
    version: str = ""
    login_code: str = ""
    reachable: bool | None = None
    reach_code: str = ""
    tables: list[TableStat] = field(default_factory=list)
    columns: list[ColumnStat] = field(default_factory=list)
    pk_count: int | None = None
    fk_count: int | None = None
    key_path: str = ""
    key_code: str = ""
    writes: bool | None = None
    priv_code: str = ""
    read_only_txn: bool | None = None
    tls_on: bool | None = None
    tls_code: str = ""
    db_bytes: int | None = None
    requested_tls: bool | None = None
    schema_code: str = ""
    sql: list[str] = field(default_factory=list)


def default_opener(
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    tls: bool,
) -> Any:
    from dms_executor.connectors import _open

    con = _open(
        kind=kind,
        host=host,
        port=int(port),
        database=database,
        user=user,
        password=password,
        tls=tls,
    )
    if kind.lower() == "sqlserver":
        try:
            con.dms_session_readonly = True
        except Exception:
            pass
    return con


def _rows(con: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    cur = con.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return [tuple(row) for row in cur.fetchall()]
    finally:
        cur.close()


def _one(con: Any, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    found = _rows(con, sql, params)
    return found[0] if found else None


def _as_int(cell: Any) -> int | None:
    if cell is None or isinstance(cell, bool):
        return None
    try:
        return int(round(float(cell)))
    except (TypeError, ValueError):
        return None


def _as_bytes(cell: Any) -> int | None:
    if cell is None or isinstance(cell, bool):
        return None
    try:
        return int(cell)
    except (TypeError, ValueError):
        return None


def tcp_open(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def tls_requested(source: dict[str, Any], driver: str) -> bool | None:
    if "tls" in source:
        return bool(source.get("tls"))
    if "encrypt" in source:
        return bool(source.get("encrypt"))
    if driver == "sqlserver":
        return True
    return None


def _login_code(exc: BaseException) -> str:
    from dms_executor.connectors import ConnectProbeError, classify_driver_error

    if isinstance(exc, ConnectProbeError):
        return exc.code
    return classify_driver_error(exc)


def _key_path(driver: str) -> str:
    if driver in {"postgresql", "gauss"}:
        return "pg_catalog"
    if driver == "sqlite":
        return "pragma"
    if driver == "oracle":
        return "all_constraints"
    return "information_schema"


def _count_sql(driver: str, schema: str, table: str) -> str:
    from dms_executor.db_connector import SourceConfig, _quote_ident

    host = "local" if driver == "sqlite" else "127.0.0.1"
    cfg = SourceConfig(
        kind=driver,  # type: ignore[arg-type]
        host=host,
        database="db" if driver != "sqlite" else schema,
        user="ro",
        password="",
    )
    return f"SELECT COUNT(*) FROM {_quote_ident(cfg, schema)}.{_quote_ident(cfg, table)}"


def _confirm_rows(con: Any, driver: str, tables: list[TableStat]) -> None:
    for table in tables:
        if table.rows not in (None, 0):
            continue
        try:
            row = _one(con, _count_sql(driver, table.schema, table.name))
        except WriteRefused:
            raise
        except Exception:
            table.row_source = "unknown"
            continue
        counted = _as_int(row[0]) if row else None
        if counted is None:
            table.row_source = "unknown"
            continue
        table.rows = counted
        table.row_source = "count"


def _privileges(con: Any, driver: str) -> tuple[bool | None, str]:
    try:
        if driver in {"postgresql", "gauss"}:
            row = _one(con, _PG_PRIV)
            if not row:
                return None, "privilege_catalog_empty"
            return bool(row[0] or row[1]), ""
        if driver == "mysql":
            found = _rows(con, _MY_PRIV)
            if not found:
                return None, "privilege_catalog_empty"
            privs = {str(item[0]).upper() for item in found}
            return bool(privs & _WRITE_PRIVS), ""
        if driver == "sqlserver":
            row = _one(con, _SS_PRIV)
            if not row:
                return None, "privilege_catalog_empty"
            return bool(row[0] or row[1]), ""
        if driver == "oracle":
            row = _one(con, _ORA_PRIV)
            if not row:
                return None, "privilege_catalog_empty"
            return bool(_as_int(row[0]) or 0), ""
        if driver == "sqlite":
            row = _one(con, "PRAGMA query_only")
            if not row:
                return None, "privilege_catalog_empty"
            return not bool(_as_int(row[0])), ""
    except WriteRefused:
        raise
    except Exception:
        return None, "privilege_catalog_unreadable"
    return None, "privilege_catalog_unreadable"


def _txn(con: Any, driver: str) -> bool | None:
    marked = getattr(con, "dms_session_readonly", None)
    raw = getattr(con, "raw", None)
    if marked is None and raw is not None:
        marked = getattr(raw, "dms_session_readonly", None)
    if driver == "sqlserver":
        return bool(marked) if marked is not None else None
    try:
        if driver in {"postgresql", "gauss"}:
            row = _one(con, "SHOW transaction_read_only")
            return str(row[0]).lower() == "on" if row else None
        if driver == "mysql":
            # MySQL 8 names it transaction_read_only. MariaDB names it tx_read_only.
            for sql in (
                "SELECT @@session.transaction_read_only",
                "SELECT @@session.tx_read_only",
            ):
                try:
                    row = _one(con, sql)
                except WriteRefused:
                    raise
                except Exception:
                    continue
                return bool(row and _as_int(row[0]) == 1)
            return None
        if driver == "sqlite":
            row = _one(con, "PRAGMA query_only")
            return bool(row and _as_int(row[0]) == 1)
        if driver == "oracle":
            return bool(marked) if marked is not None else None
    except WriteRefused:
        raise
    except Exception:
        return None
    return None


def _version(con: Any, driver: str) -> str:
    from dms_executor.drivers.gauss import server_version as gauss_version
    from dms_executor.drivers.oracle import server_version as oracle_version

    try:
        if driver == "gauss":
            return gauss_version(con)[:200]
        if driver == "oracle":
            return oracle_version(con)[:200]
        sql = {
            "postgresql": "SELECT current_setting('server_version')",
            "mysql": "SELECT VERSION()",
            "sqlserver": "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(40))",
            "sqlite": "SELECT sqlite_version()",
        }.get(driver, "")
        if not sql:
            return ""
        row = _one(con, sql)
        return "" if not row or row[0] is None else str(row[0])[:200]
    except WriteRefused:
        raise
    except Exception:
        return ""


def _tls(con: Any, driver: str, requested: bool | None) -> tuple[bool | None, str]:
    try:
        if driver in {"postgresql", "gauss"}:
            row = _one(con, "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            if not row:
                return None, ""
            return bool(row[0]), ""
        if driver == "mysql":
            row = _one(con, "SHOW STATUS LIKE 'Ssl_cipher'")
            if not row:
                return None, ""
            cipher = row[1] if len(row) > 1 else row[0]
            return bool(cipher), ""
        if driver == "sqlserver":
            if requested:
                return True, ""
            return None, ""
    except WriteRefused:
        raise
    except Exception:
        return None, "tls_unreadable"
    return None, ""


def _columns(con: Any, driver: str, database: str) -> list[ColumnStat]:
    if driver in {"postgresql", "gauss"}:
        found = _rows(con, _PG_COLS)
    elif driver == "mysql":
        found = _rows(con, _MY_COLS, (database,))
    elif driver == "sqlserver":
        found = _rows(con, _SS_COLS)
    elif driver == "oracle":
        found = _rows(con, _ORA_COLS)
    elif driver == "sqlite":
        names = _rows(
            con,
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name",
        )
        found = []
        for (name,) in names:
            for info in _rows(con, "SELECT name, type FROM pragma_table_info(?)", (name,)):
                found.append(("main", name, info[0], info[1], None, None))
    else:
        found = []
    out: list[ColumnStat] = []
    for row in found:
        if len(row) < 4:
            continue
        out.append(
            ColumnStat(
                schema=str(row[0]),
                name=str(row[1]),
                column=str(row[2]),
                data_type=str(row[3] or ""),
                precision=_as_int(row[4]) if len(row) > 4 else None,
                scale=_as_int(row[5]) if len(row) > 5 else None,
            )
        )
    return out


def _stats(con: Any, driver: str, database: str) -> tuple[list[TableStat], int | None]:
    db_bytes: int | None = None
    if driver in {"postgresql", "gauss"}:
        found = _rows(con, _PG_STATS)
    elif driver == "mysql":
        found = _rows(con, _MY_STATS, (database,))
    elif driver == "sqlserver":
        found = _rows(con, _SS_STATS)
    elif driver == "oracle":
        found = _rows(con, _ORA_STATS)
    elif driver == "sqlite":
        found = []
        names = _rows(
            con,
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name",
        )
        for (name,) in names:
            found.append(("main", name, None, None))
        pages = _one(con, "PRAGMA page_count")
        size = _one(con, "PRAGMA page_size")
        if pages and size and _as_int(pages[0]) is not None and _as_int(size[0]) is not None:
            db_bytes = int(_as_int(pages[0]) or 0) * int(_as_int(size[0]) or 0)
    else:
        found = []
    tables = [
        TableStat(
            schema=str(row[0]),
            name=str(row[1]),
            rows=_as_int(row[2]) if len(row) > 2 else None,
            nbytes=_as_bytes(row[3]) if len(row) > 3 else None,
            row_source="catalog",
        )
        for row in found
        if len(row) >= 2
    ]
    return tables, db_bytes


def _keys(con: Any, driver: str, database: str) -> tuple[int | None, int | None, str]:
    from dms_executor.db_connector import SourceConfig, list_source_keys

    host = "local" if driver == "sqlite" else "127.0.0.1"
    cfg = SourceConfig(
        kind=driver,  # type: ignore[arg-type]
        host=host,
        database=database if driver != "sqlite" else database,
        user="ro",
        password="",
    )
    if driver == "gauss":
        from dms_executor.drivers.gauss import keys_payload

        payload = keys_payload(con)
        return (
            len(payload.get("primary_keys") or {}),
            len(payload.get("foreign_keys") or []),
            "",
        )
    keys = list_source_keys(cfg, con=con)
    return len(keys.primary_keys), len(keys.foreign_keys), ""


def _sqlite_reach(database: str) -> tuple[bool, str]:
    from dms_executor.db_connector import SourceConnectionError, sqlite_source_path

    try:
        sqlite_source_path(database)
    except SourceConnectionError as exc:
        text = str(exc).lower()
        if "off" in text:
            return False, "sqlite_root_off"
        if "outside" in text:
            return False, "outside_root"
        if "not found" in text:
            return False, "not_found"
        return False, "unreachable"
    return True, ""


def _lock(con: Any, driver: str) -> None:
    from dms_executor.connectors import _lock_read_only

    if driver == "oracle":
        from dms_executor.drivers.oracle import lock_read_only

        ok = lock_read_only(con)
        try:
            con.dms_session_readonly = bool(ok)
        except Exception:
            pass
        return
    _lock_read_only(con, driver)


def collect(
    source: dict[str, Any],
    password: str,
    *,
    opener: Any = None,
    probe_network: bool = True,
) -> Facts:
    """Probe one source. The returned object has no password and no driver text."""
    from dms_executor.db_connector import _DEFAULT_PORTS

    from kits.matrix import classify

    kind = str(source.get("kind") or "").strip().lower()
    driver, accepted, reason = classify(kind)
    facts = Facts(
        kind=kind,
        driver_kind=driver,
        accepted=accepted,
        skip_reason=reason,
        requested_tls=tls_requested(source, driver),
    )
    if not accepted:
        return facts
    database = str(source.get("database") or source.get("db") or "")
    host = str(source.get("host") or "")
    user = str(source.get("user") or "")
    port_raw = source.get("port")
    if isinstance(port_raw, int) and not isinstance(port_raw, bool):
        port = port_raw
    elif isinstance(port_raw, str) and port_raw.isdigit():
        port = int(port_raw)
    else:
        port = _DEFAULT_PORTS.get(driver, 0)
    if driver != "sqlite" and password == "":
        facts.login_code = "auth"
        facts.reachable = None
        return facts
    if driver == "sqlite":
        facts.reachable, facts.reach_code = _sqlite_reach(database)
        if not facts.reachable:
            facts.login_code = "network"
            return facts
    elif probe_network:
        facts.reachable = tcp_open(host, port)
        if not facts.reachable:
            facts.reach_code = "unreachable"
            facts.login_code = "network"
            return facts
    open_fn = opener or default_opener
    tls = bool(facts.requested_tls)
    try:
        raw = open_fn(
            kind=driver,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            tls=tls,
        )
    except WriteRefused:
        facts.login_code = "write_refused"
        return facts
    except Exception as exc:
        facts.login_code = _login_code(exc)
        return facts
    guard = Guard(raw)
    try:
        try:
            _lock(guard, driver)
        except WriteRefused:
            facts.login_code = "write_refused"
            return facts
        except Exception:
            facts.read_only_txn = False
        facts.version = _version(guard, driver)
        facts.tables, facts.db_bytes = _stats(guard, driver, database)
        _confirm_rows(guard, driver, facts.tables)
        facts.writes, facts.priv_code = _privileges(guard, driver)
        if facts.read_only_txn is None:
            facts.read_only_txn = _txn(guard, driver)
        facts.tls_on, facts.tls_code = _tls(guard, driver, facts.requested_tls)
        try:
            facts.columns = _columns(guard, driver, database)
        except WriteRefused:
            raise
        except Exception:
            facts.columns = []
            facts.schema_code = "schema_unreadable"
        try:
            facts.pk_count, facts.fk_count, facts.key_code = _keys(guard, driver, database)
            facts.key_path = _key_path(driver)
        except WriteRefused:
            raise
        except Exception:
            facts.key_code = "keys_unreadable"
            facts.key_path = _key_path(driver)
    except WriteRefused:
        facts.login_code = facts.login_code or "write_refused"
    finally:
        facts.sql = list(guard.sql)
        guard.close()
    if facts.reachable is None and facts.login_code == "":
        facts.reachable = True
    return facts
