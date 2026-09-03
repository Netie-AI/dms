"""Live source-database connector - SQL Server / Azure SQL (ODBC) and MySQL.

Rows are pulled with the source's own driver and landed through
``write_bronze_rows``. Nothing here uses DuckDB's ``ATTACH``/``INSTALL``
scanners, so the hostile-SQL guard in :mod:`dms_executor.manifest` keeps
rejecting those statements everywhere, including on this path.

Drivers are imported lazily: the package must import on a machine that has
neither ``pyodbc`` nor ``pymysql`` installed.

Extract-only, by decision (DR-0005). Rows come OUT of the source; queries never go
IN. The connector holds no DuckDB handle at all - landing and provenance are
``dms_executor.bronze``'s job - and ``tests/invariants/test_extract_only.py``
asserts that on this file's source text.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dms_executor.bronze import (
    claim_source_table_name,
    mint_extracted_at,
    record_source_pull,
    write_bronze_rows,
)

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
    extracted_at: str
    #: Set when the bronze name was suffixed because another source already held the
    #: sanitised stem. Reported, never silent.
    note: str | None = None


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
    ``scripts/ontology.py``, which starts every link ``unverified`` and refuses to
    use it until it has measured it against the landed rows (DR-0005, EPIC-020).
    """

    primary_keys: dict[str, tuple[str, ...]]  # "schema.table" -> columns, ordinal order
    foreign_keys: tuple[ForeignKey, ...]

    def manifest_entry(self, pulls: list[SourcePull], *, source: str) -> dict[str, Any]:
        """The shape ``scripts/ontology.py:from_manifest`` consumes.

        ``path`` carries the bronze table name rather than a parquet path; the caller
        supplies ``relation_for`` so the compiler reads ``bronze.<table>``.
        """
        tables = []
        for pull in pulls:
            qualified = pull.source.split("#", 1)[1]
            schema, _, name = qualified.partition(".")
            tables.append({"schema": schema, "table": name, "path": pull.bronze_table})
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
                for fk in sorted(
                    self.foreign_keys, key=lambda f: (f.name, f.from_table, f.ordinal)
                )
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


def list_source_keys(cfg: SourceConfig, *, con: Any | None = None) -> SourceKeys:
    """Primary and foreign keys the source declares, from its own catalog.

    Read, not inferred. Guessing a join from column names is how a join gets
    invented; the database already says what identifies a row and what relates to
    what. These are handed to the ontology compiler as claims to be measured.
    """
    params: tuple[Any, ...]
    if cfg.kind == "sqlserver":
        pk_sql, fk_sql, params = _PK_SQLSERVER, _FK_SQLSERVER, ()
    else:
        pk_sql, fk_sql, params = _PK_MYSQL, _FK_MYSQL, (cfg.database,)
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
    return "`" + part.replace("`", "``") + "`"


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
    stem = "".join(c if c.isalnum() else "_" for c in target.qualified)
    return stem.strip("_").lower()[:60] or "source_table"


def _pull_one(
    cfg: SourceConfig,
    con: Any,
    target: SourceTable,
    *,
    max_rows: int,
    path: Path | None,
    space_id: str | None,
    bronze_table: str | None = None,
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
        name, note = claim_source_table_name(
            stem=_bronze_name(target), source=source, path=path
        )
    landed = write_bronze_rows(
        table=name,
        columns=columns,
        rows=rows,
        ref_id=ref_id,
        ingest_id=ingest_id,
        path=path,
    )
    record_source_pull(
        table_name=landed.split(".", 1)[-1],
        source=source,
        ingest_id=ingest_id,
        row_count=len(rows),
        truncated=truncated,
        space_id=space_id,
        path=path,
        extracted_at=extracted_at,
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
            cfg, con, target,
            max_rows=max_rows, path=path, space_id=space_id, bronze_table=bronze_table,
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
    source's catalog; ``scripts/ontology.py:from_manifest`` turns them into objects and
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
        pulls = [
            _pull_one(cfg, con, t, max_rows=max_rows, path=path, space_id=space_id)
            for t in wanted
        ]
    source = cfg.describe()
    return SourceExtract(
        source=source,
        pulls=pulls,
        keys=keys,
        manifest_entry=keys.manifest_entry(pulls, source=source),
        skipped=skipped,
    )
