"""Connector schema listings and the live read-only path (R2-CONNECT-CORE-01).

A harness with no database registers a callable here. Connect reads that
listing. The request body does not get to name tables the connector did not
expose.

Live reads use the source driver. They do not attach another engine. The
handshake has a connect timeout. The query does not: there is no statement
timeout and no row cap. A page is a transport window. Export streams until
the driver is done. Cancel is visible. A high or unknown plan cost warns
and does not run until the caller confirms. Nothing here is truncated in
silence.

The OpenVault secret for a network login is a JSON object with ``user`` and
``password``. It is an argument for the call and is not stored.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from dms_core.control_plane.connect_grants import clean_tables

_lock = threading.Lock()
# The callable receives the resolved credential and must not keep it.
_registry: dict[str, Callable[[str], list[str]]] = {}
_probe_override: Callable[..., dict[str, Any]] | None = None
_onto_hook: Callable[[str, tuple[str, ...]], None] | None = None
_cancel_lock = threading.Lock()
_cancels: dict[str, threading.Event] = {}

# Handshake only. The query itself has no timeout.
CONNECT_HANDSHAKE_S = 5
# Plan cost at or above this warns and waits. Not a row cap.
RUNAWAY_COST = 100_000.0
_PAGE_MAX = 5_000
_FETCH = 200

_AUTH_SQLSTATE = frozenset({"28P01", "28000"})
_PERM_SQLSTATE = frozenset({"42501"})
_NET_SQLSTATE = frozenset({"08001", "08003", "08004", "08006", "57P01", "53300"})
_AUTH_ERRNO = frozenset({1045})
_PERM_ERRNO = frozenset({1044, 1142, 1227})
_NET_ERRNO = frozenset({2002, 2003, 2005, 2006, 2013})
_TLS_ERRNO = frozenset({2026, 3159})

_SYSTEM = {
    "postgresql": {"pg_catalog", "information_schema", "pg_toast"},
    "mysql": {"mysql", "information_schema", "performance_schema", "sys"},
    "mariadb": {"mysql", "information_schema", "performance_schema", "sys"},
    "sqlite": {"temp"},
}


class ConnectProbeError(Exception):
    """Typed probe failure. The message is the code only."""

    def __init__(self, code: str) -> None:
        if code not in {"auth", "network", "permission", "tls"}:
            code = "network"
        super().__init__(code)
        self.code = code


class ConnectQueryError(Exception):
    """A query the guard or the driver refused. The message is the code only."""

    def __init__(self, code: str) -> None:
        safe = code if code and len(code) <= 80 else "bad_request"
        super().__init__(safe)
        self.code = safe


def register_connector(connector_id: str, list_tables: Callable[[str], list[str]]) -> None:
    if not connector_id or not connector_id.strip():
        raise ValueError("connector_id_required")
    _registry_set(connector_id.strip(), list_tables)


def _registry_set(connector_id: str, list_tables: Callable[[str], list[str]]) -> None:
    with _lock:
        _registry[connector_id] = list_tables


def connector_tables(connector_id: str, credential: str) -> list[str]:
    """Names the connector exposes for this in-memory credential. No row samples.

    ``credential`` is not stored and is not part of any error message.
    """
    with _lock:
        listing = _registry.get(connector_id)
    if listing is None:
        raise KeyError("connector")
    try:
        names = listing(credential)
    except Exception:
        raise ValueError("connector_failed") from None
    return list(clean_tables(names))


def set_probe_override(fn: Callable[..., dict[str, Any]] | None) -> None:
    """Test seam. Production leaves this unset."""
    global _probe_override
    _probe_override = fn


def set_auto_onto_hook(fn: Callable[[str, tuple[str, ...]], None] | None) -> None:
    """R2-AUTO-ONTO-01 registers here. The durable trigger is the job row."""
    global _onto_hook
    _onto_hook = fn


def fire_auto_onto(source_id: str, tables: tuple[str, ...]) -> None:
    hook = _onto_hook
    if hook is None:
        return
    hook(source_id, tables)


def open_cancel() -> tuple[str, threading.Event]:
    query_id = uuid4().hex
    event = threading.Event()
    with _cancel_lock:
        _cancels[query_id] = event
    return query_id, event


def cancel_live(query_id: str) -> bool:
    with _cancel_lock:
        event = _cancels.get(query_id)
    if event is None:
        return False
    event.set()
    return True


def close_cancel(query_id: str) -> None:
    with _cancel_lock:
        _cancels.pop(query_id, None)


def clear_connectors() -> None:
    global _probe_override, _onto_hook
    with _lock:
        _registry.clear()
    _probe_override = None
    _onto_hook = None
    with _cancel_lock:
        _cancels.clear()


def classify_driver_error(exc: BaseException) -> str:
    """Map a driver failure to auth, network, permission, or tls.

    The driver text is not returned. A probe that does not match a known
    code is ``network`` so the caller still gets a typed failure.
    """
    errno = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    sqlstate = getattr(exc, "sqlstate", None)
    if not isinstance(sqlstate, str):
        sqlstate = None
    name = type(exc).__name__.lower()
    if "ssl" in name or "tls" in name or errno in _TLS_ERRNO:
        return "tls"
    if sqlstate in _AUTH_SQLSTATE or errno in _AUTH_ERRNO:
        return "auth"
    if sqlstate in _PERM_SQLSTATE or errno in _PERM_ERRNO:
        return "permission"
    if isinstance(exc, (TimeoutError, ConnectionError)) or errno in _NET_ERRNO:
        return "network"
    if sqlstate in _NET_SQLSTATE:
        return "network"
    if sqlstate is not None and sqlstate.startswith("28"):
        return "auth"
    if sqlstate is not None and sqlstate.startswith("08"):
        return "network"
    text = str(exc).lower()
    if "ssl" in text or "tls" in text or "certificate" in text:
        return "tls"
    if "permission" in text or "not authorized" in text:
        return "permission"
    if "password" in text or "authentication" in text or "access denied" in text:
        return "auth"
    return "network"


def classify_query_error(exc: BaseException) -> str:
    """A query failure as a code. Syntax is ``bad_request``. Text is dropped."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == "42601":
        return "bad_request"
    if sqlstate in _PERM_SQLSTATE:
        return "permission"
    errno = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    if errno in _PERM_ERRNO:
        return "permission"
    return "bad_request"


def parse_login(secret: str) -> tuple[str, str]:
    """``{"user", "password"}`` from the OpenVault value. Failure is ``auth``."""
    try:
        body = json.loads(secret)
    except (TypeError, ValueError):
        raise ConnectProbeError("auth") from None
    if not isinstance(body, dict):
        raise ConnectProbeError("auth")
    user = body.get("user")
    password = body.get("password")
    if not isinstance(user, str) or not isinstance(password, str) or user == "":
        raise ConnectProbeError("auth")
    return user, password


def _dialect(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key in {"postgresql", "postgres"}:
        return "postgres"
    if key in {"mysql", "mariadb"}:
        return "mysql"
    if key in {"sqlserver", "tsql"}:
        return "tsql"
    if key == "sqlite":
        return "sqlite"
    raise ConnectQueryError("sql_dialect_unknown")


def guarded_reason(sql: str, *, grantable: set[str], kind: str) -> str | None:
    """None when ``sql`` is one granted read. Otherwise a refusal code.

    Writes, a second statement, ``SELECT INTO``, and row locks are refused
    here. Relations go through the existing structural allow-list.
    """
    import sqlglot
    from sqlglot import exp

    from dms_executor.grant_struct import serve_gap

    dialect = _dialect(kind)
    text = (sql or "").strip()
    if not text:
        return "ungranted:unparsed"
    try:
        trees = [tree for tree in sqlglot.parse(text, read=dialect) if tree is not None]
    except Exception:
        return "ungranted:unparsed"
    if len(trees) != 1:
        return "multi_statement"
    tree = trees[0]
    if not isinstance(tree, exp.Query):
        return "statement_not_allowed"
    names = (
        "Insert",
        "Update",
        "Delete",
        "Drop",
        "Alter",
        "Create",
        "Command",
        "Merge",
        "Copy",
        "Grant",
    )
    write_types = tuple(
        node
        for name in names
        if isinstance(node := getattr(exp, name, None), type)
    )
    for node in tree.walk():
        if isinstance(node, write_types):
            return "statement_not_allowed"
        if isinstance(node, exp.Select) and node.args.get("into") is not None:
            return "statement_not_allowed"
        if isinstance(node, exp.Lock):
            return "statement_not_allowed"
    return serve_gap(
        text, grantable=set(grantable), dialect=dialect, warehouse=None, cite_missing=False
    )


def _one_statement(sql: str) -> str:
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text


def plan_cost(con: Any, kind: str, statement: str) -> float | None:
    """Total plan cost, or None when the driver has no number. None warns."""
    key = kind.lower()
    cur = con.cursor()
    try:
        if key in {"postgresql", "postgres"}:
            cur.execute("EXPLAIN (FORMAT JSON) " + statement)
        elif key in {"mysql", "mariadb"}:
            cur.execute("EXPLAIN FORMAT=JSON " + statement)
        else:
            return None
        row = cur.fetchone()
    except Exception:
        return None
    finally:
        cur.close()
    if not row:
        return None
    payload: Any = row[0]
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    return _find_cost(payload)


def _find_cost(node: Any) -> float | None:
    if isinstance(node, dict):
        for key in ("Total Cost", "query_cost", "cost", "read_cost"):
            found = _as_float(node.get(key))
            if found is not None:
                return found
        for value in node.values():
            found = _find_cost(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_cost(item)
            if found is not None:
                return found
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _blocked(estimate: float | None, confirm: bool) -> bool:
    if confirm:
        return False
    if estimate is None:
        return True
    return estimate >= RUNAWAY_COST


def probe_source(
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    tls: bool = False,
) -> dict[str, Any]:
    """Connect, read the server version, and count visible base tables.

    ``tables`` is for the save path. The HTTP test response drops it.
    """
    if _probe_override is not None:
        try:
            return _probe_override(
                kind=kind,
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                tls=tls,
            )
        except ConnectProbeError:
            raise
        except Exception as exc:
            raise ConnectProbeError(classify_driver_error(exc)) from None
    started = time.perf_counter()
    try:
        with _session(
            kind=kind,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            tls=tls,
        ) as con:
            version = _version(con, kind)
            tables = _tables(con, kind, database)
    except ConnectProbeError:
        raise
    except Exception as exc:
        raise ConnectProbeError(classify_driver_error(exc)) from None
    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "server_version": version[:200],
        "tables_visible": len(tables),
        "tables": tables,
    }


@contextmanager
def _session(
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    tls: bool,
) -> Iterator[Any]:
    con = _open(
        kind=kind,
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        tls=tls,
    )
    try:
        _lock_read_only(con, kind)
        yield con
    finally:
        try:
            con.rollback()
        except Exception:
            pass
        try:
            con.close()
        except Exception:
            pass


def _open(
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    tls: bool,
) -> Any:
    key = kind.lower()
    try:
        if key in {"postgresql", "postgres"}:
            import psycopg

            return psycopg.connect(
                host=host,
                port=int(port),
                user=user,
                password=password,
                dbname=database,
                connect_timeout=CONNECT_HANDSHAKE_S,
                sslmode="require" if tls else "prefer",
            )
        if key in {"mysql", "mariadb"}:
            import pymysql

            kwargs: dict[str, Any] = {
                "host": host,
                "port": int(port),
                "user": user,
                "password": password,
                "database": database,
                "connect_timeout": CONNECT_HANDSHAKE_S,
            }
            if tls:
                import ssl

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                kwargs["ssl"] = ctx
            return pymysql.connect(**kwargs)
        if key == "sqlite":
            from dms_executor.db_connector import sqlite_source_path

            path = sqlite_source_path(database)
            con = sqlite3.connect(
                f"file:{quote(str(path))}?mode=ro",
                uri=True,
                timeout=CONNECT_HANDSHAKE_S,
            )
            return con
        if key == "sqlserver":
            import pyodbc

            from dms_executor.db_connector import SourceConfig, _sqlserver_connection_string

            cfg = SourceConfig(
                kind="sqlserver",
                host=host,
                database=database,
                user=user,
                password=password,
                port=port,
                encrypt=True,
                trust_server_certificate=not tls,
            )
            return pyodbc.connect(
                _sqlserver_connection_string(cfg),
                readonly=True,
                timeout=CONNECT_HANDSHAKE_S,
            )
    except ConnectProbeError:
        raise
    except Exception as exc:
        raise ConnectProbeError(classify_driver_error(exc)) from None
    raise ConnectProbeError("network")


def _lock_read_only(con: Any, kind: str) -> None:
    key = kind.lower()
    try:
        if key in {"postgresql", "postgres"}:
            con.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            con.commit()
            con.execute("SET TRANSACTION READ ONLY")
            return
        if key in {"mysql", "mariadb"}:
            cur = con.cursor()
            try:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                con.commit()
                cur.execute("START TRANSACTION READ ONLY")
            finally:
                cur.close()
            return
        if key == "sqlite":
            con.execute("PRAGMA query_only = ON")
    except ConnectProbeError:
        raise
    except Exception:
        raise ConnectProbeError("permission") from None


def _version(con: Any, kind: str) -> str:
    key = kind.lower()
    if key in {"postgresql", "postgres"}:
        sql = "SELECT current_setting('server_version')"
    elif key in {"mysql", "mariadb"}:
        sql = "SELECT VERSION()"
    elif key == "sqlite":
        sql = "SELECT sqlite_version()"
    else:
        return ""
    cur = con.cursor()
    try:
        cur.execute(sql)
        row = cur.fetchone()
    finally:
        cur.close()
    if not row or row[0] is None:
        return ""
    return str(row[0])


def _tables(con: Any, kind: str, database: str) -> list[str]:
    key = kind.lower()
    if key in {"mysql", "mariadb"}:
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = %s "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        params: tuple[Any, ...] = (database,)
    elif key == "sqlite":
        sql = (
            "SELECT 'main', name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
            "ORDER BY name"
        )
        params = ()
    elif key in {"postgresql", "postgres", "sqlserver"}:
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        params = ()
    else:
        return []
    skip = _SYSTEM.get(key, set())
    cur = con.cursor()
    try:
        cur.execute(sql, params) if params else cur.execute(sql)
        rows = cur.fetchall()
    finally:
        cur.close()
    out: list[str] = []
    for schema, name in rows:
        if str(schema).lower() in skip:
            continue
        try:
            qualified = clean_tables([f"{schema}.{name}"])
        except ValueError:
            # ponytail: identifiers outside the grant grammar are omitted.
            # Upgrade path is quoted identifiers once the grant checker accepts them.
            continue
        out.extend(qualified)
    return out


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[binary]"
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return "[unreadable]"
    return str(value)


def _columns(description: Any) -> list[str]:
    if not description:
        return []
    return [str(item[0]) for item in description]


def _row_dict(columns: list[str], values: tuple[Any, ...]) -> dict[str, Any]:
    return {columns[i]: _cell(values[i]) for i in range(min(len(columns), len(values)))}


def _warning(estimate: float | None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "runaway",
        "warning": "runaway",
        "estimate": estimate,
        "rows": [],
        "columns": [],
        "complete": False,
        "next_offset": None,
        "cancelled": False,
        "truncated": False,
        "bounded": False,
    }


def _ready(
    con: Any,
    *,
    kind: str,
    sql: str,
    grantable: set[str],
    confirm_runaway: bool,
) -> tuple[str, float | None] | dict[str, Any]:
    reason = guarded_reason(sql, grantable=grantable, kind=kind)
    if reason:
        raise ConnectQueryError(reason)
    statement = _one_statement(sql)
    estimate = plan_cost(con, kind, statement)
    if _blocked(estimate, confirm_runaway):
        return _warning(estimate)
    return statement, estimate


def run_live_select(
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sql: str,
    grantable: set[str],
    tls: bool = False,
    offset: int = 0,
    page_size: int = 500,
    confirm_runaway: bool = False,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """One page of a guarded read. ``complete`` is false when another page exists.

    The LIMIT here is the page, not a cap on the result. The next offset is
    returned. ``truncated`` and ``bounded`` stay false.
    """
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise ConnectQueryError("bad_request")
    if page_size < 1 or page_size > _PAGE_MAX or offset < 0:
        raise ConnectQueryError("bad_request")
    if cancel is not None and cancel.is_set():
        return {
            "ok": False,
            "error": "cancelled",
            "warning": None,
            "estimate": None,
            "rows": [],
            "columns": [],
            "complete": False,
            "next_offset": None,
            "cancelled": True,
            "truncated": False,
            "bounded": False,
        }
    try:
        with _session(
            kind=kind,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            tls=tls,
        ) as con:
            ready = _ready(
                con,
                kind=kind,
                sql=sql,
                grantable=grantable,
                confirm_runaway=confirm_runaway,
            )
            if isinstance(ready, dict):
                return ready
            statement, estimate = ready
            wrapped = (
                f"SELECT * FROM ({statement}) AS _dms_page "
                f"LIMIT {int(page_size) + 1} OFFSET {int(offset)}"
            )
            cur = con.cursor()
            try:
                cur.execute(wrapped)
                fetched = cur.fetchall()
                columns = _columns(cur.description)
            finally:
                cur.close()
    except (ConnectProbeError, ConnectQueryError):
        raise
    except Exception as exc:
        raise ConnectQueryError(classify_query_error(exc)) from None
    more = len(fetched) > page_size
    page = fetched[:page_size]
    return {
        "ok": True,
        "error": None,
        "warning": None,
        "estimate": estimate,
        "columns": columns,
        "rows": [_row_dict(columns, tuple(item)) for item in page],
        "complete": not more,
        "next_offset": None if not more else offset + page_size,
        "cancelled": False,
        "truncated": False,
        "bounded": False,
    }


def iter_live_export(
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sql: str,
    grantable: set[str],
    tls: bool = False,
    confirm_runaway: bool = False,
    cancel: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream every row. No LIMIT. Stops on Cancel and says so."""
    try:
        with _session(
            kind=kind,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            tls=tls,
        ) as con:
            ready = _ready(
                con,
                kind=kind,
                sql=sql,
                grantable=grantable,
                confirm_runaway=confirm_runaway,
            )
            if isinstance(ready, dict):
                yield ready
                return
            statement, estimate = ready
            yield {
                "event": "header",
                "ok": True,
                "estimate": estimate,
                "truncated": False,
                "bounded": False,
            }
            cur = con.cursor()
            try:
                cur.execute(statement)
                columns = _columns(cur.description)
                while True:
                    if cancel is not None and cancel.is_set():
                        yield {
                            "event": "end",
                            "complete": False,
                            "cancelled": True,
                            "truncated": False,
                            "bounded": False,
                        }
                        return
                    batch = cur.fetchmany(_FETCH)
                    if not batch:
                        break
                    for item in batch:
                        yield {"event": "row", "row": _row_dict(columns, tuple(item))}
            finally:
                cur.close()
    except (ConnectProbeError, ConnectQueryError) as exc:
        yield {"event": "error", "error": exc.code, "truncated": False, "bounded": False}
        return
    except Exception as exc:
        yield {
            "event": "error",
            "error": classify_query_error(exc),
            "truncated": False,
            "bounded": False,
        }
        return
    yield {
        "event": "end",
        "complete": True,
        "cancelled": False,
        "truncated": False,
        "bounded": False,
    }
