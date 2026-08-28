"""Live source-database connector — SQL Server / Azure SQL (ODBC) and MySQL.

Rows are pulled with the source's own driver and landed through
``write_bronze_rows``. Nothing here uses DuckDB's ``ATTACH``/``INSTALL``
scanners, so the hostile-SQL guard in :mod:`dms_executor.manifest` keeps
rejecting those statements everywhere, including on this path.

Drivers are imported lazily: the package must import on a machine that has
neither ``pyodbc`` nor ``pymysql`` installed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dms_executor.bronze import write_bronze_rows

SourceKind = Literal["sqlserver", "mysql"]

#: Ceiling on a single pull. ``write_bronze_rows`` materialises every row in
#: memory before the INSERT, so an unbounded pull against a production table is
#: an OOM, not a slow query.
DEFAULT_MAX_ROWS = 500_000

DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
_DEFAULT_PORTS: dict[str, int] = {"sqlserver": 1433, "mysql": 3306}


class SourceConnectionError(RuntimeError):
    """Connect/auth failed, or the required driver is not installed."""


class UnknownSourceTable(LookupError):
    """Asked for a table the source does not expose to this login."""


@dataclass(frozen=True)
class SourceConfig:
    """Connection details for one source database.

    ``password`` is ``repr=False`` so a config never leaks into a traceback,
    a log line, or a ``%r`` format.
    """

    kind: SourceKind
    host: str
    database: str
    user: str
    password: str = field(default="", repr=False)
    port: int | None = None
    #: Azure SQL rejects unencrypted connections; keep this on unless a local
    #: dev instance genuinely has no certificate.
    encrypt: bool = True
    trust_server_certificate: bool = False
    odbc_driver: str = DEFAULT_ODBC_DRIVER
    connect_timeout: int = 30

    def __post_init__(self) -> None:
        if self.kind not in _DEFAULT_PORTS:
            raise ValueError(f"unsupported source kind: {self.kind!r}")
        if not self.host or not self.database:
            raise ValueError("host and database are required")

    @property
    def resolved_port(self) -> int:
        return self.port or _DEFAULT_PORTS[self.kind]

    def describe(self) -> str:
        """Credential-free provenance string, safe to store and display."""
        return f"{self.kind}://{self.host}:{self.resolved_port}/{self.database}"


@dataclass(frozen=True)
class SourceTable:
    schema: str
    name: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class SourcePull:
    """What one ingest actually landed."""

    bronze_table: str
    source: str
    columns: list[str]
    row_count: int
    truncated: bool
    ingest_id: str
    ref_id: str


def _odbc_value(value: str) -> str:
    """Brace-quote an ODBC connection-string value.

    Unquoted values break on ``;``; a literal ``}`` inside braces is escaped by
    doubling it.
    """
    return "{" + str(value).replace("}", "}}") + "}"


def _sqlserver_connection_string(cfg: SourceConfig) -> str:
    server = f"{cfg.host},{cfg.resolved_port}"
    parts = [
        f"DRIVER={_odbc_value(cfg.odbc_driver)}",
        f"SERVER={_odbc_value(server)}",
        f"DATABASE={_odbc_value(cfg.database)}",
        f"UID={_odbc_value(cfg.user)}",
        f"PWD={_odbc_value(cfg.password)}",
        f"Encrypt={'yes' if cfg.encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if cfg.trust_server_certificate else 'no'}",
        f"Connection Timeout={int(cfg.connect_timeout)}",
    ]
    return ";".join(parts) + ";"


@contextmanager
def connect(cfg: SourceConfig) -> Iterator[Any]:
    """Open a read connection to the source, closing it on the way out."""
    con: Any
    if cfg.kind == "sqlserver":
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise SourceConnectionError(
                "pyodbc is not installed; Azure SQL / SQL Server sources are unavailable"
            ) from exc
        try:
            con = pyodbc.connect(_sqlserver_connection_string(cfg), readonly=True)
        except Exception as exc:
            raise SourceConnectionError(f"could not connect to {cfg.describe()}") from exc
    else:
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise SourceConnectionError(
                "pymysql is not installed; MySQL sources are unavailable"
            ) from exc
        try:
            con = pymysql.connect(
                host=cfg.host,
                port=cfg.resolved_port,
                user=cfg.user,
                password=cfg.password,
                database=cfg.database,
                connect_timeout=cfg.connect_timeout,
            )
        except Exception as exc:
            raise SourceConnectionError(f"could not connect to {cfg.describe()}") from exc
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001 - close must not mask the real error
            pass


_SYSTEM_SCHEMAS: dict[str, set[str]] = {
    "sqlserver": {"sys", "INFORMATION_SCHEMA", "guest", "db_owner", "db_accessadmin"},
    "mysql": {"mysql", "information_schema", "performance_schema", "sys"},
}


def list_source_tables(cfg: SourceConfig, *, con: Any | None = None) -> list[SourceTable]:
    """Base tables this login can see, system schemas excluded."""
    if cfg.kind == "sqlserver":
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        params: tuple[Any, ...] = ()
    else:
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = %s "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        params = (cfg.database,)

    def _run(active: Any) -> list[SourceTable]:
        cur = active.cursor()
        try:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            skip = _SYSTEM_SCHEMAS[cfg.kind]
            return [
                SourceTable(schema=str(r[0]), name=str(r[1]))
                for r in cur.fetchall()
                if str(r[0]) not in skip
            ]
        finally:
            cur.close()

    if con is not None:
        return _run(con)
    with connect(cfg) as opened:
        return _run(opened)


def _quote_ident(cfg: SourceConfig, part: str) -> str:
    if cfg.kind == "sqlserver":
        return "[" + part.replace("]", "]]") + "]"
    return "`" + part.replace("`", "``") + "`"


def _resolve(cfg: SourceConfig, con: Any, schema: str | None, table: str) -> SourceTable:
    """Match a requested table against what the source actually exposes.

    Identifiers cannot be parameterised, so the only safe source of an
    identifier is the server's own catalog — never the caller's string.
    """
    available = list_source_tables(cfg, con=con)
    wanted = table.lower()
    hits = [
        t
        for t in available
        if t.name.lower() == wanted and (schema is None or t.schema.lower() == schema.lower())
    ]
    if not hits:
        raise UnknownSourceTable(f"{schema or '*'}.{table} not found on {cfg.describe()}")
    if len(hits) > 1:
        names = ", ".join(t.qualified for t in hits)
        raise UnknownSourceTable(f"{table} is ambiguous; qualify the schema ({names})")
    return hits[0]


def _fetch(
    cfg: SourceConfig,
    con: Any,
    target: SourceTable,
    *,
    max_rows: int,
) -> tuple[list[str], list[list[Any]], bool]:
    ident = f"{_quote_ident(cfg, target.schema)}.{_quote_ident(cfg, target.name)}"
    cur = con.cursor()
    try:
        cur.execute(f"SELECT * FROM {ident}")
        columns = [str(d[0]) for d in cur.description]
        # Ask for one extra row: if it arrives, the pull was capped.
        fetched = list(cur.fetchmany(max_rows + 1))
        truncated = len(fetched) > max_rows
        rows = [[None if v is None else str(v) for v in row] for row in fetched[:max_rows]]
        return columns, rows, truncated
    finally:
        cur.close()


def preview_source_table(
    cfg: SourceConfig,
    table: str,
    *,
    schema: str | None = None,
    limit: int = 50,
) -> tuple[list[str], list[list[Any]]]:
    """Read the first ``limit`` rows without writing anything to bronze."""
    with connect(cfg) as con:
        target = _resolve(cfg, con, schema, table)
        columns, rows, _ = _fetch(cfg, con, target, max_rows=max(1, limit))
    return columns, rows


def _bronze_name(target: SourceTable) -> str:
    stem = "".join(c if c.isalnum() else "_" for c in target.qualified)
    return stem.strip("_").lower()[:60] or "source_table"


def ingest_source_table(
    cfg: SourceConfig,
    table: str,
    *,
    schema: str | None = None,
    bronze_table: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    path: Path | None = None,
) -> SourcePull:
    """Pull one source table and land it in bronze with provenance."""
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    ingest_id = str(uuid.uuid4())
    ref_id = str(uuid.uuid4())
    with connect(cfg) as con:
        target = _resolve(cfg, con, schema, table)
        columns, rows, truncated = _fetch(cfg, con, target, max_rows=max_rows)
    if not columns:
        raise ValueError(f"{target.qualified} exposed no columns")
    landed = write_bronze_rows(
        table=bronze_table or _bronze_name(target),
        columns=columns,
        rows=rows,
        ref_id=ref_id,
        ingest_id=ingest_id,
        path=path,
    )
    return SourcePull(
        bronze_table=landed,
        source=f"{cfg.describe()}#{target.qualified}",
        columns=columns,
        row_count=len(rows),
        truncated=truncated,
        ingest_id=ingest_id,
        ref_id=ref_id,
    )
