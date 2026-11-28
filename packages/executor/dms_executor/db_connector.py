"""Live source-database connector - SQL Server / Azure SQL (ODBC), MySQL, PostgreSQL,
and SQLite files (stdlib ``sqlite3``, read-only, only under ``DMS_SQLITE_SOURCE_ROOT``).

Rows are pulled with the source's own driver and landed through
``write_bronze_rows``. Nothing here uses DuckDB's ``ATTACH``/``INSTALL``
scanners, so the hostile-SQL guard in :mod:`dms_executor.manifest` keeps
rejecting those statements everywhere, including on this path.

Drivers are imported lazily: the package must import on a machine that has
neither ``pyodbc`` nor ``pymysql`` installed. PostgreSQL uses ``psycopg``
(already required by the DMS app for the control plane) the same way.

Extract-only, by decision (DR-0005). Rows come OUT of the source; queries never go
IN. The connector holds no DuckDB handle at all - landing and provenance are
``dms_executor.bronze``'s job - and ``tests/invariants/test_extract_only.py``
asserts that on this file's source text.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dms_executor.bronze import (
    claim_source_table_name,
    has_non_ascii,
    ident_safe,
    mint_extracted_at,
    record_source_pull,
    type_bronze_columns,
    unicode_ident_enabled,
    write_bronze_rows,
)

SourceKind = Literal["sqlserver", "mysql", "postgresql", "sqlite", "gauss", "oracle"]

#: Ceiling on a single pull. ``write_bronze_rows`` materialises every row in
#: memory before the INSERT, so an unbounded pull against a production table is
#: an OOM, not a slow query.
DEFAULT_MAX_ROWS = 500_000

DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
_DEFAULT_PORTS: dict[str, int] = {
    "sqlserver": 1433,
    "mysql": 3306,
    "postgresql": 5432,
    "sqlite": 0,
    "gauss": 5432,
    "oracle": 1521,
}


def _accept_kind(kind: str) -> bool:
    """Gauss and Oracle stay unsupported until their flag is on.

    Off, SourceConfig raises the same ValueError an unknown kind raises.
    """
    if kind in {"sqlserver", "mysql", "postgresql", "sqlite"}:
        return True
    if kind == "gauss":
        from dms_executor.drivers.gauss import enabled

        return enabled()
    if kind == "oracle":
        from dms_executor.drivers.oracle import enabled

        return enabled()
    return False

#: SQLite sources are files on the DMS host. Off unless this names a directory; a
#: path outside it (after resolving symlinks and ``..``) is refused, so an API caller
#: cannot point the connector at an arbitrary file (FDE-SQLITE-SOURCE-01).
SQLITE_ROOT_ENV = "DMS_SQLITE_SOURCE_ROOT"


class SourceConnectionError(RuntimeError):
    """Connect/auth failed, or the required driver is not installed.

    ``code`` and ``doc`` are set by a typed driver failure. The message stays
    the credential-free description. ``doc`` is a public URL or empty.
    """

    def __init__(self, message: str, *, code: str = "", doc: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.doc = doc


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
        if self.kind not in _DEFAULT_PORTS or not _accept_kind(self.kind):
            raise ValueError(f"unsupported source kind: {self.kind!r}")
        if self.kind == "sqlite":
            # database is the file path; host is not used. "#" would split the
            # provenance string ``<describe>#<schema>.<table>`` in the wrong place.
            if not self.database or "#" in self.database:
                raise ValueError("database (the SQLite file path) is required")
            return
        if not self.host or not self.database:
            raise ValueError("host and database are required")

    @property
    def resolved_port(self) -> int:
        return self.port or _DEFAULT_PORTS[self.kind]

    def describe(self) -> str:
        """Credential-free provenance string, safe to store and display."""
        if self.kind == "sqlite":
            return f"sqlite:///{self.database.lstrip('/')}"
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
    extracted_at: str
    #: Set when the bronze name was suffixed because another source already held the
    #: sanitised stem. Reported, never silent.
    note: str | None = None
    #: Landed DuckDB type per column, from the source's declared types.
    column_types: dict[str, str] = field(default_factory=dict)
    #: Columns that stayed VARCHAR, and why (undeclared type, unmapped type, or
    #: values the declared type could not hold). Named on the receipt, never silent.
    untyped_columns: dict[str, str] = field(default_factory=dict)
    #: Original qualified table name. Set only when ``DMS_UNICODE_IDENT`` is on.
    source_name: str | None = None
    #: Landed column identifier -> original header. Same flag.
    column_sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ForeignKey:
    """One column of one declared foreign key.

    A multi-column key is several of these sharing ``name``, ordered by ``ordinal``.
    """

    name: str
    from_table: str  # schema.table, the child
    from_column: str
    to_table: str  # schema.table, the parent
    to_column: str
    ordinal: int


@dataclass(frozen=True)
class SourceKeys:
    """What the source DECLARES about its keys. Claims, not facts.

    A declared foreign key says a value should exist in the parent. It does not say
    the parent side is unique on those columns, and uniqueness is the only property
    that makes a join safe to group through. So these are read here and handed to
    ``dms_executor.ontology``, which starts every link ``unverified`` and refuses to
    use it until it has measured it against the landed rows (DR-0005, EPIC-020).
    """

    primary_keys: dict[str, tuple[str, ...]]  # "schema.table" -> columns, ordinal order
    foreign_keys: tuple[ForeignKey, ...]

    def manifest_entry(self, pulls: list[SourcePull], *, source: str) -> dict[str, Any]:
        """The shape ``dms_executor.ontology.from_manifest`` consumes.

        ``path`` carries the bronze table name rather than a parquet path; the caller
        supplies ``relation_for`` so the compiler reads ``bronze.<table>``.
        """
        tables = []
        for pull in pulls:
            qualified = pull.source.split("#", 1)[1]
            schema, _, name = qualified.partition(".")
            tables.append(
                {
                    "schema": schema,
                    "table": name,
                    "path": pull.bronze_table,
                    "truncated": pull.truncated,
                }
            )
        return {
            "source": source,
            "tables": tables,
            "primary_keys": {k: list(v) for k, v in self.primary_keys.items()},
            "foreign_keys": [
                {
                    "name": fk.name,
                    "from_table": fk.from_table,
                    "from_column": fk.from_column,
                    "to_table": fk.to_table,
                    "to_column": fk.to_column,
                }
                for fk in sorted(self.foreign_keys, key=lambda f: (f.name, f.from_table, f.ordinal))
            ],
        }


@dataclass
class SourceExtract:
    """Everything one database pull produced: rows landed, keys declared, and the
    manifest the ontology compiler reads."""

    source: str
    pulls: list[SourcePull]
    keys: SourceKeys
    manifest_entry: dict[str, Any]
    #: Requested by name but not exposed to this login. Reported, never silently
    #: dropped - a table the steward asked for and did not get is news.
    skipped: list[str] = field(default_factory=list)
    #: Qualified names ``list_source_tables`` returned on this connection.
    #: Names only. No row samples.
    exposed_tables: list[str] = field(default_factory=list)


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


def sqlite_source_path(database: str) -> Path:
    """The SQLite file for ``database``, inside ``DMS_SQLITE_SOURCE_ROOT``, or refuse."""
    import os

    root_raw = os.environ.get(SQLITE_ROOT_ENV, "").strip()
    if not root_raw:
        raise SourceConnectionError(
            f"SQLite sources are off; set {SQLITE_ROOT_ENV} to the directory that holds them"
        )
    root = Path(root_raw).resolve()
    candidate = Path(database)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise SourceConnectionError(f"SQLite file is outside {SQLITE_ROOT_ENV}")
    if not resolved.is_file():
        raise SourceConnectionError("SQLite file not found")
    return resolved


def _sqlite_connect(cfg: SourceConfig) -> Any:
    """Read-only by URI (``mode=ro``): the connector never writes the customer's file."""
    import sqlite3
    from urllib.parse import quote

    path = sqlite_source_path(cfg.database)
    try:
        con = sqlite3.connect(
            f"file:{quote(str(path))}?mode=ro", uri=True, timeout=cfg.connect_timeout
        )
        con.execute("PRAGMA query_only = ON")
    except Exception as exc:
        raise SourceConnectionError(f"could not open {cfg.describe()}") from exc
    return con


def _force_readonly(con: Any, kind: str, cfg: SourceConfig) -> None:
    """Session read-only when the harness flag is on. The password is not copied."""
    from dms_core.harness_sec import readonly_session_sql

    sql = readonly_session_sql(kind)
    if not sql:
        return
    try:
        if kind == "mysql":
            cur = con.cursor()
            try:
                cur.execute(sql)
            finally:
                cur.close()
        else:
            con.execute(sql)
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        raise SourceConnectionError(f"could not connect to {cfg.describe()}") from None


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
    elif cfg.kind == "postgresql":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise SourceConnectionError(
                "psycopg is not installed; PostgreSQL sources are unavailable"
            ) from exc
        try:
            con = psycopg.connect(
                host=cfg.host,
                port=cfg.resolved_port,
                user=cfg.user,
                password=cfg.password,
                dbname=cfg.database,
                connect_timeout=cfg.connect_timeout,
            )
        except Exception as exc:
            raise SourceConnectionError(f"could not connect to {cfg.describe()}") from exc
        _force_readonly(con, "postgresql", cfg)
    elif cfg.kind == "mysql":
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
        _force_readonly(con, "mysql", cfg)
    elif cfg.kind == "sqlite":
        con = _sqlite_connect(cfg)
    elif cfg.kind == "gauss":
        from dms_executor.drivers.gauss import open_source

        con = open_source(cfg)
    elif cfg.kind == "oracle":
        from dms_executor.drivers.oracle import open_source

        con = open_source(cfg)
    else:
        raise SourceConnectionError(f"unsupported source kind: {cfg.kind!r}")
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
    "postgresql": {"pg_catalog", "information_schema", "pg_toast"},
    "sqlite": {"temp"},
    "gauss": {"pg_catalog", "information_schema", "pg_toast"},
    "oracle": {
        "SYS",
        "SYSTEM",
        "XDB",
        "CTXSYS",
        "MDSYS",
        "OLAPSYS",
        "ORDSYS",
        "OUTLN",
        "DBSNMP",
        "WMSYS",
        "APPQOSSYS",
        "GSMADMIN_INTERNAL",
        "OJVMSYS",
        "LBACSYS",
        "DVSYS",
    },
}


def _run_query(con: Any, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    cur = con.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def list_source_tables(cfg: SourceConfig, *, con: Any | None = None) -> list[SourceTable]:
    """Base tables this login can see, system schemas excluded."""
    if cfg.kind == "sqlite":
        sql = (
            "SELECT 'main', name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"
        )
        params: tuple[Any, ...] = ()
    elif cfg.kind == "mysql":
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = %s "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        params = (cfg.database,)
    elif cfg.kind == "oracle":
        from dms_executor.drivers.oracle import TABLES_SQL

        sql = TABLES_SQL
        params = ()
    else:
        # SQL Server and PostgreSQL: database is not a schema. MySQL's filter
        # TABLE_SCHEMA = database would find zero tables on bird_minidev/public.
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        params = ()

    def _run(active: Any) -> list[SourceTable]:
        skip = _SYSTEM_SCHEMAS[cfg.kind]
        return [
            SourceTable(schema=str(r[0]), name=str(r[1]))
            for r in _run_query(active, sql, params)
            if str(r[0]) not in skip
        ]

    if con is not None:
        return _run(con)
    with connect(cfg) as opened:
        return _run(opened)


# Both dialects are asked for the same eight FK columns and the same four PK columns,
# in the same order, so one parser serves both. The queries differ because SQL Server
# splits the relationship across REFERENTIAL_CONSTRAINTS and two KEY_COLUMN_USAGE
# rows, while MySQL puts the referenced side on the same KEY_COLUMN_USAGE row.
_PK_SQLSERVER = (
    "SELECT kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.COLUMN_NAME, kcu.ORDINAL_POSITION "
    "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
    "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
    "  ON kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA "
    " AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME "
    " AND kcu.TABLE_SCHEMA = tc.TABLE_SCHEMA AND kcu.TABLE_NAME = tc.TABLE_NAME "
    "WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' "
    "ORDER BY kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.ORDINAL_POSITION"
)
# Joined on CONSTRAINT_SCHEMA as well as CONSTRAINT_NAME. Constraint names are
# unique per schema in SQL Server, not per database, so two schemas may each carry
# an FK_Customer; joining on the name alone would cross-match them.
_FK_SQLSERVER = (
    "SELECT rc.CONSTRAINT_NAME, "
    "       c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, "
    "       p.TABLE_SCHEMA, p.TABLE_NAME, p.COLUMN_NAME, c.ORDINAL_POSITION "
    "FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc "
    "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE c "
    "  ON c.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA "
    " AND c.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
    "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE p "
    "  ON p.CONSTRAINT_SCHEMA = rc.UNIQUE_CONSTRAINT_SCHEMA "
    " AND p.CONSTRAINT_NAME = rc.UNIQUE_CONSTRAINT_NAME "
    " AND p.ORDINAL_POSITION = c.ORDINAL_POSITION "
    "ORDER BY rc.CONSTRAINT_NAME, c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION"
)
_PK_MYSQL = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION "
    "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = %s AND CONSTRAINT_NAME = 'PRIMARY' "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)
_FK_MYSQL = (
    "SELECT CONSTRAINT_NAME, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, "
    "       REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, "
    "       ORDINAL_POSITION "
    "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
    "ORDER BY CONSTRAINT_NAME, TABLE_NAME, ORDINAL_POSITION"
)


# PostgreSQL reads keys from pg_catalog, not INFORMATION_SCHEMA. The standard views
# show a constraint only to a role that owns the table or holds a privilege other
# than SELECT, so the SELECT-only login an extract should use read zero primary and
# zero foreign keys (measured on PostgreSQL 17, FDE-PG-KEYS-01). pg_constraint has
# no such filter; has_table_privilege keeps the answer to tables this login can
# read, the same set list_source_tables returns. unnest(conkey, confkey) pairs each
# child column with its parent column by position, so a composite key maps
# column-for-column instead of by ORDINAL_POSITION of the parent's unique index.
_PK_POSTGRES = (
    "SELECT n.nspname, c.relname, a.attname, k.ord "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class c ON c.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum "
    "WHERE con.contype = 'p' AND has_table_privilege(c.oid, 'SELECT') "
    "ORDER BY n.nspname, c.relname, k.ord"
)
_FK_POSTGRES = (
    "SELECT con.conname, n.nspname, c.relname, a.attname, "
    "       pn.nspname, pc.relname, pa.attname, k.ord "
    "FROM pg_catalog.pg_constraint con "
    "JOIN pg_catalog.pg_class c ON c.oid = con.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_class pc ON pc.oid = con.confrelid "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace "
    "CROSS JOIN LATERAL unnest(con.conkey, con.confkey) "
    "  WITH ORDINALITY AS k(attnum, fattnum, ord) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum "
    "JOIN pg_catalog.pg_attribute pa ON pa.attrelid = pc.oid AND pa.attnum = k.fattnum "
    "WHERE con.contype = 'f' "
    "  AND has_table_privilege(c.oid, 'SELECT') AND has_table_privilege(pc.oid, 'SELECT') "
    "ORDER BY con.conname, n.nspname, c.relname, k.ord"
)


def _sqlite_keys(cfg: SourceConfig, con: Any) -> SourceKeys:
    """PKs from ``pragma_table_info``, FKs from ``pragma_foreign_key_list``.

    SQLite lets an FK omit the parent columns (``REFERENCES parent``), meaning the
    parent's primary key; those are filled from the parent's PK by position.
    """
    tables = list_source_tables(cfg, con=con)
    pks: dict[str, list[str]] = {}
    for t in tables:
        rows = _run_query(
            con,
            "SELECT name, pk FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk",
            (t.name,),
        )
        if rows:
            pks[t.qualified] = [str(r[0]) for r in rows]
    known = {t.name.lower(): t for t in tables}
    fks: list[ForeignKey] = []
    for t in tables:
        rows = _run_query(
            con,
            'SELECT id, seq, "table", "from", "to" FROM pragma_foreign_key_list(?) '
            "ORDER BY id, seq",
            (t.name,),
        )
        for fk_id, seq, parent, child_col, parent_col in rows:
            target = known.get(str(parent).lower())
            if target is None:
                continue  # parent not visible: a claim we cannot name, so not made
            if parent_col is None:
                parent_pk = pks.get(target.qualified, [])
                if int(seq) >= len(parent_pk):
                    continue
                parent_col = parent_pk[int(seq)]
            fks.append(
                ForeignKey(
                    name=f"{t.name}_fk{fk_id}",
                    from_table=t.qualified,
                    from_column=str(child_col),
                    to_table=target.qualified,
                    to_column=str(parent_col),
                    ordinal=int(seq) + 1,
                )
            )
    return SourceKeys(
        primary_keys={k: tuple(v) for k, v in pks.items()},
        foreign_keys=tuple(fks),
    )


def list_source_keys(cfg: SourceConfig, *, con: Any | None = None) -> SourceKeys:
    """Primary and foreign keys the source declares, from its own catalog.

    Read, not inferred. Guessing a join from column names is how a join gets
    invented; the database already says what identifies a row and what relates to
    what. These are handed to the ontology compiler as claims to be measured.
    """
    if cfg.kind == "sqlite":
        if con is not None:
            return _sqlite_keys(cfg, con)
        with connect(cfg) as opened:
            return _sqlite_keys(cfg, opened)
    params: tuple[Any, ...]
    if cfg.kind == "mysql":
        pk_sql, fk_sql, params = _PK_MYSQL, _FK_MYSQL, (cfg.database,)
    elif cfg.kind in {"postgresql", "gauss"}:
        # Not the MySQL TABLE_SCHEMA = database filter, and not INFORMATION_SCHEMA
        # (blind to a SELECT-only login); see _PK_POSTGRES. Gauss PG mode uses
        # the same catalog statements (FDE-PG-KEYS-01).
        pk_sql, fk_sql, params = _PK_POSTGRES, _FK_POSTGRES, ()
    elif cfg.kind == "oracle":
        from dms_executor.drivers.oracle import FK_SQL, PK_SQL

        pk_sql, fk_sql, params = PK_SQL, FK_SQL, ()
    else:
        pk_sql, fk_sql, params = _PK_SQLSERVER, _FK_SQLSERVER, ()
    skip = _SYSTEM_SCHEMAS[cfg.kind]

    def _run(active: Any) -> SourceKeys:
        pks: dict[str, list[str]] = {}
        for schema, table, column, _ordinal in _run_query(active, pk_sql, params):
            if str(schema) in skip:
                continue
            pks.setdefault(f"{schema}.{table}", []).append(str(column))
        fks: list[ForeignKey] = []
        for row in _run_query(active, fk_sql, params):
            name, cs, ct, cc, ps, pt, pc, ordinal = row
            if str(cs) in skip or str(ps) in skip:
                continue
            fks.append(
                ForeignKey(
                    name=str(name),
                    from_table=f"{cs}.{ct}",
                    from_column=str(cc),
                    to_table=f"{ps}.{pt}",
                    to_column=str(pc),
                    ordinal=int(ordinal),
                )
            )
        return SourceKeys(
            primary_keys={k: tuple(v) for k, v in pks.items()},
            foreign_keys=tuple(fks),
        )

    if con is not None:
        return _run(con)
    with connect(cfg) as opened:
        return _run(opened)


def _quote_ident(cfg: SourceConfig, part: str) -> str:
    if cfg.kind == "sqlserver":
        return "[" + part.replace("]", "]]") + "]"
    if cfg.kind == "mysql":
        return "`" + part.replace("`", "``") + "`"
    return '"' + part.replace('"', '""') + '"'


def _resolve(cfg: SourceConfig, con: Any, schema: str | None, table: str) -> SourceTable:
    """Match a requested table against what the source actually exposes.

    Identifiers cannot be parameterised, so the only safe source of an
    identifier is the server's own catalog - never the caller's string.
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
    full = target.qualified
    if unicode_ident_enabled() and has_non_ascii(full):
        return ident_safe(full)
    stem = "".join(c if c.isalnum() else "_" for c in full)
    return ident_safe(stem.strip("_").lower()[:60] or "source_table")


#: INFORMATION_SCHEMA.COLUMNS answers the same shape on all three sources.
_COLUMN_TYPES_SQL = (
    "SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE "
    "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = {p} AND TABLE_NAME = {p}"
)


def list_source_column_types(
    cfg: SourceConfig, con: Any, target: SourceTable, columns: list[str]
) -> dict[str, tuple[str, int | None, int | None]] | None:
    """Declared type of each fetched column, from the source's own catalog.

    ``None`` when the catalog will not say (no answer, an error, or rows that do
    not name exactly the fetched columns): the pull then lands untyped and the
    receipt says so. Declared, never inferred from the values.
    """
    if cfg.kind == "sqlite":
        return _sqlite_column_types(con, target, columns)
    placeholder = "?" if cfg.kind == "sqlserver" else "%s"
    try:
        rows = _run_query(
            con,
            _COLUMN_TYPES_SQL.format(p=placeholder),
            (target.schema, target.name),
        )
    except Exception:  # noqa: BLE001 - an unreadable catalog is "undeclared"
        return None
    out: dict[str, tuple[str, int | None, int | None]] = {}
    for row in rows:
        if len(row) < 4 or not isinstance(row[0], str) or not isinstance(row[1], str):
            return None
        prec = int(row[2]) if isinstance(row[2], int) else None
        scale = int(row[3]) if isinstance(row[3], int) else None
        out[row[0]] = (row[1], prec, scale)
    if set(out) != set(columns):
        return None
    return out


_SQLITE_DECL = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$")


def _sqlite_column_types(
    con: Any, target: SourceTable, columns: list[str]
) -> dict[str, tuple[str, int | None, int | None]] | None:
    """Declared types from ``pragma_table_info``: ``NUMERIC(12,2)`` -> ("numeric", 12, 2).

    A column declared with no type, or with one this cannot read, is left out, which
    lands the pull untyped and says so. SQLite stores values by affinity, so the
    bronze typing step still checks every value converts exactly before typing.
    """
    try:
        rows = _run_query(con, "SELECT name, type FROM pragma_table_info(?)", (target.name,))
    except Exception:  # noqa: BLE001 - an unreadable catalog is "undeclared"
        return None
    out: dict[str, tuple[str, int | None, int | None]] = {}
    for name, decl in rows:
        m = _SQLITE_DECL.match(str(decl or ""))
        if not m:
            return None
        kind = " ".join(m.group(1).lower().split())
        prec = int(m.group(2)) if m.group(2) else None
        scale = int(m.group(3)) if m.group(3) else (0 if prec is not None else None)
        out[str(name)] = (kind, prec, scale)
    if set(out) != set(columns):
        return None
    return out


def _distinct_columns(columns: list[str]) -> tuple[list[str], dict[str, str]]:
    """Column names unique case-insensitively; ``{original: landed}`` for the renamed."""
    seen: set[str] = set()
    out: list[str] = []
    renamed: dict[str, str] = {}
    for col in columns:
        name, n = col, 2
        while name.lower() in seen:
            name, n = f"{col}_{n}", n + 1
        seen.add(name.lower())
        out.append(name)
        if name != col:
            renamed[col] = name
    return out, renamed


def _pull_one(
    cfg: SourceConfig,
    con: Any,
    target: SourceTable,
    *,
    max_rows: int,
    path: Path | None,
    space_id: str | None,
    bronze_table: str | None = None,
    claimed: tuple[str, str | None] | None = None,
) -> SourcePull:
    """Land one table in bronze with both halves of its provenance.

    Every row gets ``_src`` and ``_ingest_id`` from ``write_bronze_rows``; the table
    gets a registry row naming the source from ``record_source_pull``. The parked
    connector did only the first, which is row provenance with no source provenance -
    half an answer (DR-0005 part 4).
    """
    ingest_id = str(uuid.uuid4())
    ref_id = str(uuid.uuid4())
    extracted_at = mint_extracted_at()
    columns, rows, truncated = _fetch(cfg, con, target, max_rows=max_rows)
    if not columns:
        raise ValueError(f"{target.qualified} exposed no columns")
    source = f"{cfg.describe()}#{target.qualified}"
    # A caller-named bronze_table is taken as given. A derived one goes through the
    # registry's claim so two source tables one alnum-stem apart cannot overwrite each
    # other - the second gets a suffix and the receipt carries a note.
    note: str | None = None
    name = bronze_table
    if name is None:
        name, note = claimed or claim_source_table_name(
            stem=_bronze_name(target), source=source, path=path, space_id=space_id
        )
    declared = list_source_column_types(cfg, con, target, columns)
    landed_cols, renamed = _distinct_columns(columns)
    if renamed:
        # DuckDB column names are case-insensitive: ``Name`` and ``name`` in one
        # source table were a CatalogException and a 500. The duplicate lands
        # under a suffixed name and the receipt says which.
        rename_note = "; ".join(f"column {a!r} landed as {b!r}" for a, b in renamed.items())
        note = f"{note}; {rename_note}" if note else rename_note
        if declared is not None:
            declared = {renamed.get(c, c): v for c, v in declared.items()}
    column_sources: dict[str, str] = {}
    if unicode_ident_enabled():
        origin = {new: old for old, new in renamed.items()}
        final: list[str] = []
        used: set[str] = set()
        redeclared: dict[str, tuple[str, int | None, int | None]] | None = (
            {} if declared is not None else None
        )
        for col in landed_cols:
            # Do not reuse ``name``: that is the bronze table, and the last
            # column would overwrite it.
            col_name = ident_safe(col) if has_non_ascii(col) else col
            base, n = col_name, 2
            while col_name.lower() in used:
                col_name = f"{base}_{n}"
                n += 1
            used.add(col_name.lower())
            final.append(col_name)
            column_sources[col_name] = origin.get(col, col)
            if redeclared is not None and declared is not None and col in declared:
                redeclared[col_name] = declared[col]
        landed_cols = final
        if redeclared is not None:
            declared = redeclared
    landed = write_bronze_rows(
        table=name,
        columns=landed_cols,
        rows=rows,
        ref_id=ref_id,
        ingest_id=ingest_id,
        path=path,
    )
    typing = type_bronze_columns(table=landed, declared=declared, path=path)
    record_source_pull(
        table_name=landed.split(".", 1)[-1],
        source=source,
        ingest_id=ingest_id,
        row_count=len(rows),
        truncated=truncated,
        space_id=space_id,
        path=path,
        extracted_at=extracted_at,
        untyped_numeric=typing["untyped_numeric"],
        source_name=target.qualified if column_sources else None,
        column_sources=column_sources or None,
    )
    return SourcePull(
        bronze_table=landed,
        source=source,
        columns=columns,
        row_count=len(rows),
        truncated=truncated,
        ingest_id=ingest_id,
        ref_id=ref_id,
        extracted_at=extracted_at,
        note=note,
        column_types=typing["column_types"],
        untyped_columns=typing["untyped_columns"],
        source_name=target.qualified if column_sources else None,
        column_sources=column_sources,
    )


def ingest_source_table(
    cfg: SourceConfig,
    table: str,
    *,
    schema: str | None = None,
    bronze_table: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    path: Path | None = None,
    space_id: str | None = None,
) -> SourcePull:
    """Pull one source table and land it in bronze with provenance."""
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    with connect(cfg) as con:
        target = _resolve(cfg, con, schema, table)
        return _pull_one(
            cfg,
            con,
            target,
            max_rows=max_rows,
            path=path,
            space_id=space_id,
            bronze_table=bronze_table,
        )


def ingest_source_database(
    cfg: SourceConfig,
    *,
    tables: list[str] | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    path: Path | None = None,
    space_id: str | None = None,
) -> SourceExtract:
    """Collect and understand: every base table to bronze, plus the keys to compile.

    One connection for the whole pull. ``tables`` narrows it by ``schema.table`` or
    bare name; a requested table the login cannot see is reported in ``skipped``,
    never dropped in silence.

    What this returns is deliberately NOT an ontology. The keys are claims from the
    source's catalog; ``dms_executor.ontology.from_manifest`` turns them into objects and
    links, and ``verify()`` measures every link before ``compile()`` will use it. This
    function is the end of "collect" and the start of "understand" - it does not
    finish the second.
    """
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    with connect(cfg) as con:
        available = list_source_tables(cfg, con=con)
        wanted: list[SourceTable] = available
        skipped: list[str] = []
        if tables is not None:
            wanted = []
            by_qualified = {t.qualified.lower(): t for t in available}
            by_name: dict[str, list[SourceTable]] = {}
            for t in available:
                by_name.setdefault(t.name.lower(), []).append(t)
            for req in tables:
                key = req.lower()
                if key in by_qualified:
                    wanted.append(by_qualified[key])
                elif len(by_name.get(key, [])) == 1:
                    wanted.append(by_name[key][0])
                else:
                    skipped.append(req)
        keys = list_source_keys(cfg, con=con)
        # Claim every bronze name before writing any rows: a claim refused for the
        # third table used to leave the first two landed and granted with no
        # receipt (adversary round 4).
        claims: list[tuple[str, str | None]] = []
        reserved: dict[str, tuple[Any, Any]] = {}
        for t in wanted:
            source = f"{cfg.describe()}#{t.qualified}"
            claim = claim_source_table_name(
                stem=_bronze_name(t),
                source=source,
                path=path,
                space_id=space_id,
                reserved=reserved,
            )
            # Nothing is registered until rows land, so two tables in this pull
            # that sanitise alike (dbo.a-b, dbo.a_b) must see each other's claim.
            reserved[claim[0]] = (source, space_id)
            claims.append(claim)
        pulls = [
            _pull_one(cfg, con, t, max_rows=max_rows, path=path, space_id=space_id, claimed=claim)
            for t, claim in zip(wanted, claims, strict=True)
        ]
    source = cfg.describe()
    if space_id and path is not None:
        try:
            from dms_executor.schema_context import note_serving_source

            note_serving_source(path, space_id)
        except Exception:  # noqa: BLE001 -- connect must not fail the pull
            pass
    return SourceExtract(
        source=source,
        pulls=pulls,
        keys=keys,
        manifest_entry=keys.manifest_entry(pulls, source=source),
        skipped=skipped,
        exposed_tables=[t.qualified for t in available],
    )
