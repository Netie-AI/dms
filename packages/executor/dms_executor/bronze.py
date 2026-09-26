"""Bronze writer — every row gets _src STRUCT[] + _ingest_id (Appendix A)."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_warehouse import (
    WarehouseBusy,
    connect_file,
    connect_file_readonly,
    connect_readonly,
    ensure_demo_warehouse,
    warehouse_path,
)
from dms_executor.duckdb_scalar import scalar_int
from dms_executor.lake_schema import ensure_lake_schemas


@dataclass
class IngestReceipt:
    files_seen: int
    ingested: int
    quarantined: int
    reasons: list[dict[str, str]]
    ingest_id: str
    source_ref_id: str
    table: str | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ident_safe(stem: str) -> str:
    """A bronze name every layer accepts: ASCII ``[A-Za-z_][A-Za-z0-9_]*``.

    ``str.isalnum`` keeps ``é`` and a name may start with a digit
    (``2024_sales``). Either breaks the SHARED NAMING RULE, and
    ``cortex_row_predicates`` then refuses the whole Space's manifest, so one
    such table made every ask on the Space abstain ``submit_failed``.
    """
    out = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in stem)
    if not out or not (out[0].isalpha() or out[0] == "_"):
        out = f"t_{out}"
    return out


def _safe_table_stem(filename: str) -> str:
    return ident_safe("".join(c if c.isalnum() else "_" for c in Path(filename).stem)[:40])


def bronze_table_for_sheet(filename: str, sheet: str | None = None) -> str:
    """Ident ingest writes: ``{stem}_{sheet}``, alnum/underscore, 40 chars, ``bronze.`` prefix."""
    stem = Path(filename).stem
    if sheet:
        stem = f"{stem}_{sheet}"
    ident = ident_safe("".join(c if c.isalnum() else "_" for c in stem)[:40])
    return f"bronze.{ident}"


#: Which source file each bronze table was built from. Bronze tables carry
#: ``_ingest_id`` per row but nothing recorded the *file*, so a second upload
#: could take over an existing table's name with no way to tell afterwards that
#: it had ever belonged to something else.
_REGISTRY = "bronze._ingest_registry"
_REGISTRY_LOCK = threading.Lock()


def mint_extracted_at() -> str:
    """One UTC clock for a pull. Same string on the receipt, preview, tree, and envelope."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def classify_source_kind(filename: str | None) -> str:
    """SQL pulls use SourceConfig.describe() as filename; everything else is a file."""
    if filename and filename.startswith(("sqlserver://", "mysql://", "postgresql://")):
        return "sql"
    return "file"


def _ensure_registry(con: duckdb.DuckDBPyConnection) -> None:
    # Library fires /tree twice. Two ALTER ADD COLUMN on the same catalog 500s.
    with _REGISTRY_LOCK:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_REGISTRY} (
              table_name VARCHAR PRIMARY KEY,
              filename   VARCHAR,
              sha256     VARCHAR,
              ingest_id  VARCHAR,
              created_at TIMESTAMPTZ
            )
            """
        )
        cols = {
            r[0]
            for r in con.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'bronze' AND table_name = '_ingest_registry'
                """
            ).fetchall()
        }
        if "space_id" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN space_id VARCHAR")
        # Source provenance for SQL pulls (DR-0005 part 4). These were folded into the
        # sha256 fingerprint and nothing could read them back - a one-way function is
        # not a field. Same widen-if-missing idiom as space_id above.
        if "row_count" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN row_count INTEGER")
        if "truncated" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN truncated BOOLEAN")
        # VARCHAR, not TIMESTAMPTZ: DuckDB's str(created_at) is not the Python mint, and
        # the four artifacts have to show one identical string (SQLSRC-05).
        if "extracted_at" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN extracted_at VARCHAR")
        if "source_kind" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN source_kind VARCHAR")
        # How many rows the SOURCE held when a pull was capped (DEFAULT_MAX_ROWS).
        # ``truncated`` alone said "partial" without saying how partial; a steward
        # cannot judge 500,000 of 1,056,320 from a boolean. NULL = not capped, or
        # the source would not say.
        if "source_row_count" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN source_row_count BIGINT")
        # Columns the source declared numeric that landed VARCHAR (dms#277 F-e),
        # comma-separated. The ask path refuses SQL reading them: text compares
        # '9.50' > '100.25'.
        if "untyped_numeric" not in cols:
            con.execute(f"ALTER TABLE {_REGISTRY} ADD COLUMN untyped_numeric VARCHAR")


def _claim_table_name(
    con: duckdb.DuckDBPyConnection,
    *,
    stem: str,
    filename: str,
    digest: str,
    space_scoped: bool = False,
    space_id: str | None = None,
    reserved: dict[str, tuple[Any, Any]] | None = None,
) -> tuple[str, str | None]:
    """Return (table_name, collision_note) for this file.

    ``_safe_table_stem`` keys on ``Path(filename).stem``, so ``2023/sales.csv``
    and ``2024/sales.csv`` both resolve to ``sales`` — and ingest then ran an
    unconditional ``DROP TABLE``. Uploading a folder destroyed one file with the
    other while the receipt reported both as ingested, and the shipped folder
    picker makes that a single click.

    Re-uploading the *same* file keeps overwriting its own table, which is what
    a person means by re-ingesting. A *different* file gets its own name,
    disambiguated by a digest of the full path, and the collision is reported
    rather than resolved in silence.
    """
    _ensure_registry(con)

    def _owner(name: str) -> tuple[Any, Any] | None:
        # Names claimed earlier in this same batch, not yet in the registry.
        if reserved and name in reserved:
            return reserved[name]
        row = con.execute(
            f"SELECT filename, space_id FROM {_REGISTRY} WHERE table_name = ?", [name]
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def _mine(owner: tuple[Any, Any] | None) -> bool:
        if owner is None:
            return True
        if owner[0] != filename:
            return False
        # ONTO-DERIVE-01 (F-d): the same SQL source pulled into another Space is
        # another owner. Keyed on the source alone, Space B's pull rewrote Space
        # A's table and re-tagged it B's, so A's grant and stored ontology
        # pointed at rows it no longer owned.
        return not space_scoped or _canonical_space(owner[1]) == (space_id or None)

    first = _owner(stem)
    if _mine(first):
        return stem, None
    key = f"{filename}|{space_id or ''}" if space_scoped else filename
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    alt = f"{stem[:31]}_{suffix}"
    assert first is not None
    held = first[0] if not space_scoped or first[0] != filename else f"{first[0]} (another Space)"
    if not _mine(_owner(alt)):
        raise ValueError(f"bronze names {stem!r} and {alt!r} are both held by other sources")
    return alt, f"name {stem!r} already holds {held!r}; stored separately"


def _record_ingest(
    con: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    filename: str,
    digest: str,
    ingest_id: str,
    space_id: str | None = None,
    row_count: int | None = None,
    truncated: bool | None = None,
    extracted_at: str | None = None,
    source_kind: str | None = None,
    source_row_count: int | None = None,
) -> None:
    con.execute(f"DELETE FROM {_REGISTRY} WHERE table_name = ?", [table_name])
    kind = source_kind or classify_source_kind(filename)
    stamp = extracted_at or mint_extracted_at()
    # Named columns, not positional VALUES. The registry has been widened three
    # times; a positional insert silently misaligns the moment a column is added.
    # created_at is the same mint so CSV and SQL share one clock; now() is not used.
    con.execute(
        f"INSERT INTO {_REGISTRY} "
        "(table_name, filename, sha256, ingest_id, created_at, space_id, row_count, "
        "truncated, extracted_at, source_kind, source_row_count) "
        "VALUES (?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), ?, ?, ?, ?, ?, ?)",
        [
            table_name,
            filename,
            digest,
            ingest_id,
            stamp,
            space_id,
            row_count,
            truncated,
            stamp,
            kind,
            source_row_count,
        ],
    )


def claim_source_table_name(
    *,
    stem: str,
    source: str,
    path: Path | None = None,
    space_id: str | None = None,
    reserved: dict[str, tuple[Any, Any]] | None = None,
) -> tuple[str, str | None]:
    """Reserve a bronze name for a SQL-sourced table without taking another table's.

    The connector's name sanitiser is lossy: ``dbo.a-b`` and ``dbo.a_b`` both become
    ``dbo_a_b``, and any two names sharing a 60-character prefix collide. The parked
    connector wrote straight to that name, so the second pull DROPped the first while the
    receipt reported both as landed - a silent overwrite (R-0011). The file path already
    had the answer in ``_claim_table_name``: same stem held by a *different* source gets a
    suffix and a note. This is that claim, keyed on the credential-free source string.

    Returns ``(table_name, collision_note)``. The note is ``None`` when nothing collided.
    """
    db = ensure_demo_warehouse(path or warehouse_path())
    con = connect_file(db)
    try:
        ensure_lake_schemas(con)
        _ensure_registry(con)
        return _claim_table_name(
            con,
            stem=ident_safe(stem),
            filename=source,
            digest="",
            space_scoped=True,
            space_id=_canonical_space(space_id),
            reserved=reserved,
        )
    finally:
        con.close()


def _canonical_space(space_id: str | None) -> str | None:
    if not space_id:
        return None
    from dms_executor.demo_grants import canonical_space_id

    return canonical_space_id(space_id)


def record_source_pull(
    *,
    table_name: str,
    source: str,
    ingest_id: str,
    row_count: int,
    truncated: bool,
    space_id: str | None = None,
    path: Path | None = None,
    extracted_at: str | None = None,
    source_row_count: int | None = None,
    untyped_numeric: list[str] | None = None,
) -> str:
    """Name the SQL source a bronze table was pulled from (DR-0005 part 4).

    The registry was built for files: ``filename`` and a content ``sha256``. A SQL pull
    has neither, and the parked connector wrote none of this, so a SQL-sourced table
    carried row provenance (``_src``) and no source provenance - half an answer.

    ``filename`` holds the credential-free source string
    (``sqlserver://`` / ``mysql://`` / ``postgresql://host:port/db#schema.table``).
    ``sha256`` holds a fingerprint of the pull - source, row count, truncation -
    so a re-pull that landed a different number of rows is detectable as a
    different ingest rather than silently the same one.
    ``extracted_at`` / ``source_kind`` / ``row_count`` / ``truncated`` are real columns
    (widened, not a sidecar) because a one-way fingerprint cannot be read back.

    Lives here, not in the connector, because the connector must never hold a DuckDB
    handle: extract-only is asserted on its source text
    (``tests/invariants/test_extract_only.py``).
    """
    fingerprint = hashlib.sha256(
        f"{source}|rows={row_count}|truncated={truncated}".encode()
    ).hexdigest()
    stamp = extracted_at or mint_extracted_at()
    db = ensure_demo_warehouse(path or warehouse_path())
    con = connect_file(db)
    try:
        ensure_lake_schemas(con)
        _ensure_registry(con)
        _record_ingest(
            con,
            table_name=table_name,
            filename=source,
            digest=fingerprint,
            ingest_id=ingest_id,
            space_id=space_id,
            row_count=row_count,
            truncated=truncated,
            extracted_at=stamp,
            source_kind="sql",
            source_row_count=source_row_count,
        )
        con.execute(
            f"UPDATE {_REGISTRY} SET untyped_numeric = ? WHERE table_name = ?",
            [",".join(untyped_numeric or []) or None, table_name],
        )
    finally:
        con.close()
    _note_recorded_pull(
        path,
        table_name,
        truncated=truncated,
        row_count=row_count,
        source_row_count=source_row_count,
    )
    return fingerprint


def lookup_ingest_watermarks(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """One read-only pass over the registry. Does not seed a warehouse (P-DMS-34)."""
    db = path or warehouse_path()
    if not Path(db).is_file():
        return {}
    con = connect_file(Path(db))
    try:
        rows = con.execute(
            f"SELECT table_name, filename, extracted_at, truncated, source_kind "
            f"FROM {_REGISTRY}"
        ).fetchall()
    except Exception:  # noqa: BLE001 - registry or columns may not exist yet
        return {}
    finally:
        con.close()
    out: dict[str, dict[str, Any]] = {}
    for name, filename, extracted_at, truncated, source_kind in rows:
        rec = {
            "source": None if filename is None else str(filename),
            "extracted_at": None if extracted_at is None else str(extracted_at),
            "truncated": None if truncated is None else bool(truncated),
            "source_kind": source_kind or classify_source_kind(
                None if filename is None else str(filename)
            ),
        }
        for alias in (name, f"bronze.{name}", f"bronze:{name}"):
            out[alias] = rec
    return out


def stamp_contributing_source_watermarks(
    sources: list[dict[str, Any]],
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Attach extracted_at / source_kind after Cortex normalisation. Unknown -> null."""
    marks = lookup_ingest_watermarks(path=path)
    stamped: list[dict[str, Any]] = []
    for src in sources:
        item = dict(src)
        container = str(item.get("container") or "")
        rec = marks.get(container)
        if rec is None and container.startswith("bronze:"):
            rec = marks.get(container.removeprefix("bronze:"))
        if rec is None and container.startswith("bronze."):
            rec = marks.get(container.removeprefix("bronze."))
        if rec is None and "." in container:
            rec = marks.get(container.rsplit(".", 1)[-1])
        item["extracted_at"] = rec["extracted_at"] if rec else None
        item["source_kind"] = rec["source_kind"] if rec else None
        stamped.append(item)
    return stamped


#: Registry columns a read may name. Anything the warehouse's registry predates
#: reads as NULL instead of failing the whole query.
_REGISTRY_OPTIONAL = (
    "space_id",
    "row_count",
    "truncated",
    "extracted_at",
    "source_kind",
    "source_row_count",
    "untyped_numeric",
)


def _registry_rows(
    build: Callable[[Callable[[str], str]], str],
    params: list[Any],
    *,
    path: Path | None,
) -> list[tuple[Any, ...]]:
    """Read the ingest registry read-only, without seeding or widening it.

    ``build(col)`` returns the SQL; ``col("x")`` yields ``r.x`` when the column
    exists and ``NULL`` when this registry predates it. A warehouse written before
    ``source_row_count`` existed (the BIRD warehouse) used to fail the whole
    SELECT. "No warehouse" and "no registry yet" read as empty.

    Opens read-only (``connect_file_readonly``): these back GET routes, and a GET
    must not take the write lock an ingest in another process may hold. When the
    warehouse cannot be read - a writer holds it, the file is unreadable - this
    raises ``WarehouseBusy`` (named, ``code = "warehouse_unavailable"``) for the
    caller to degrade on. It never returns ``[]`` for "could not look": that is
    how a Space with 75 landed tables read as holding none.
    """
    db = Path(path or warehouse_path())
    if not db.is_file():
        return []
    con = connect_file_readonly(db)
    try:
        try:
            cols = {
                str(r[0])
                for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'bronze' AND table_name = '_ingest_registry'"
                ).fetchall()
            }
            if not cols:
                return []

            def col(name: str) -> str:
                if name in cols:
                    return f"r.{name}"
                if name in _REGISTRY_OPTIONAL:
                    return "NULL"
                raise KeyError(f"unknown registry column {name!r}")

            return [tuple(r) for r in con.execute(build(col), params).fetchall()]
        except duckdb.Error as exc:
            raise WarehouseBusy(f"ingest registry unreadable: {exc}") from exc
    finally:
        con.close()


def _truncation_fields(
    row_count: Any, truncated: Any, source_row_count: Any
) -> dict[str, Any]:
    """``truncated`` / ``loaded_rows`` / ``source_row_count`` / ``partial`` for one pull."""
    loaded = None if row_count is None else int(row_count)
    total = None if source_row_count is None else int(source_row_count)
    is_trunc = bool(truncated)
    partial = None
    if is_trunc:
        got = "?" if loaded is None else f"{loaded:,}"
        of = "an unknown number of" if total is None else f"{total:,}"
        partial = f"partial: {got} of {of} source rows (ingest row cap)"
    return {
        "truncated": is_trunc,
        "loaded_rows": loaded,
        "source_row_count": total,
        "partial": partial,
    }


def list_source_pulls(
    *,
    space_id: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """SQL-source pulls in the ingest registry, as Space sources.

    ``POST /v1/studio/sources/sql`` records every landed table here with its
    ``space_id`` (``record_source_pull``), and nothing read it back for the Space:
    a Space holding 75 landed tables reported ``source_count: 0``. This is that
    read. Only tables that still exist in bronze are listed, and ``truncated``
    rides along with the loaded and source row counts, so a capped pull is
    visible on the sources API rather than only on the one ingest receipt.
    """
    from dms_executor.demo_grants import canonical_space_id

    rows = _registry_rows(
        lambda col: f"""
        SELECT r.table_name, r.filename, {col("space_id")}, {col("row_count")},
               {col("truncated")}, {col("source_row_count")}, {col("extracted_at")},
               r.ingest_id, {col("source_kind")}
          FROM {_REGISTRY} r
          JOIN information_schema.tables t
            ON t.table_schema = 'bronze' AND t.table_name = r.table_name
         ORDER BY r.table_name
        """,
        [],
        path=path,
    )
    want = canonical_space_id(space_id) if space_id else None
    out: list[dict[str, Any]] = []
    for row in rows:
        name, filename, row_space, row_count, truncated, total, extracted_at, ingest_id = row[:8]
        # A registry older than ``source_kind`` still names a SQL pull by its
        # SourceConfig.describe() filename; classify it the way ingest would.
        kind = row[8] or classify_source_kind(None if filename is None else str(filename))
        if kind != "sql":
            continue
        canon = canonical_space_id(str(row_space)) if row_space else None
        if want is not None and canon != want:
            continue
        entry: dict[str, Any] = {
            "id": f"bronze:{name}",
            "kind": "sql",
            "ref": None if filename is None else str(filename),
            "scope": "team" if canon else "company",
            "space_id": canon,
            "space_name": None,
            "bronze_table": f"bronze.{name}",
            "extracted_at": None if extracted_at is None else str(extracted_at),
            "ingest_id": None if ingest_id is None else str(ingest_id),
        }
        entry.update(_truncation_fields(row_count, truncated, total))
        out.append(entry)
    return out


#: Last complete read of the capped tables, per warehouse file:
#: ``{bare_name: (loaded_rows, source_rows)}``. Lets an answer that cites a capped
#: table still say so while an ingest in another process holds the file.
_TRUNC_SNAPSHOT: dict[str, dict[str, tuple[Any, Any]]] = {}
#: Pulls this process recorded since that snapshot (None = no longer capped).
_TRUNC_RECENT: dict[str, dict[str, tuple[Any, Any] | None]] = {}
_TRUNC_GUARD = threading.Lock()

ROWCAP_UNAVAILABLE_NOTE = (
    "ingest row-cap check unavailable (ingest registry busy or unreadable); "
    "this answer reads bronze table(s) that may be partial"
)


def _trunc_key(path: Path | None) -> str:
    return str(Path(path or warehouse_path()).resolve())


def _note_recorded_pull(
    path: Path | None, table: str, *, truncated: bool | None, row_count: int | None,
    source_row_count: int | None,
) -> None:
    """Keep the row-cap view current for pulls this process just recorded."""
    with _TRUNC_GUARD:
        recent = _TRUNC_RECENT.setdefault(_trunc_key(path), {})
        recent[str(table).lower()] = (row_count, source_row_count) if truncated else None


def _capped_tables(
    path: Path | None,
) -> tuple[dict[str, tuple[Any, Any]], frozenset[str] | None]:
    """(capped tables, names whose state is known).

    The second item is None when the registry was read (every table's state is
    known), else the names this process recorded since the last read it could not
    make - a busy warehouse with no snapshot knows only those.
    """
    key = _trunc_key(path)
    try:
        rows = _registry_rows(
            lambda col: (
                f"SELECT r.table_name, {col('row_count')}, {col('source_row_count')} "
                f"FROM {_REGISTRY} r WHERE {col('truncated')}"
            ),
            [],
            path=path,
        )
    except WarehouseBusy:
        with _TRUNC_GUARD:
            snap = _TRUNC_SNAPSHOT.get(key)
            known = dict(snap or {})
            for name, val in (_TRUNC_RECENT.get(key) or {}).items():
                if val is None:
                    known.pop(name, None)
                else:
                    known[name] = val
            recent_names = frozenset(_TRUNC_RECENT.get(key) or {})
        return known, None if snap is not None else recent_names
    fresh = {str(r[0]).lower(): (r[1], r[2]) for r in rows}
    with _TRUNC_GUARD:
        _TRUNC_SNAPSHOT[key] = fresh
        _TRUNC_RECENT.pop(key, None)
    return fresh, None


def truncation_notes(
    *,
    tables: list[str],
    sql: str | None = None,
    path: Path | None = None,
) -> list[str]:
    """Assumption lines for every capped bronze table an answer read - and only those.

    A table landed under the row cap answers over the rows that landed, not the
    source. Saying nothing is the silent-fallback lie: a total over 500,000 of
    1,056,320 rows reads as the whole ledger. Matching is on the bare bronze name
    in ``tables`` (sources / grounded tables) or on a table the SQL reads.

    When the registry is busy (an ingest in another process holds the file) the
    last complete read, plus pulls this process recorded since, still decides. Only
    if nothing was ever read *and* the answer reads an explicitly ``bronze.``-
    qualified table does it say the check was unavailable: an answer over the demo
    tables or any uncapped table carries no row-cap line, busy or not.
    """
    bare: set[str] = set()
    bronze_named: set[str] = set()
    for t in tables:
        label = str(t or "").strip().strip('"')
        qualified = label.lower().startswith(("bronze.", "bronze:"))
        for prefix in ("bronze:", "bronze."):
            label = label.removeprefix(prefix)
        if label:
            name = label.rsplit(".", 1)[-1].strip('"').lower()
            bare.add(name)
            if qualified:
                bronze_named.add(name)
    sql_l = (sql or "").lower()
    # Table references only, so a column that shares a capped table's name is not
    # a hit. Unparseable SQL falls back to the bare-identifier match: a spurious
    # partial line is noise, a missing one is the silent lie.
    read_tables = None
    if sql_l:
        from dms_executor.sql_currency import referenced_tables

        read_tables = referenced_tables(sql or "")
        if read_tables is not None:
            bronze_named |= {
                t.split(".", 1)[1] for t in read_tables if t.startswith("bronze.")
            }
    if not bare and not sql_l:
        return []
    capped, known_only = _capped_tables(path)
    notes: list[str] = []
    for key in sorted(capped):
        row_count, total = capped[key]
        if key in bare:
            hit = True
        elif not sql_l:
            hit = False
        elif read_tables is not None:
            hit = key in read_tables
        else:
            hit = re.search(rf'(?<![\w$]){re.escape(key)}(?![\w$])', sql_l) is not None
        if not hit:
            continue
        loaded = "?" if row_count is None else f"{int(row_count):,}"
        of = "an unknown number of" if total is None else f"{int(total):,}"
        notes.append(
            f"partial table: bronze.{key} holds {loaded} of {of} source rows "
            "(ingest row cap); this answer covers the loaded rows only"
        )
    if known_only is not None and not notes and (bronze_named - known_only):
        notes.append(ROWCAP_UNAVAILABLE_NOTE)
    return notes


def ingest_csv_bytes(
    *,
    filename: str,
    data: bytes,
    path: Path | None = None,
    table_name: str | None = None,
    space_id: str | None = None,
) -> IngestReceipt:
    """Write CSV into bronze.<table> with _src array provenance."""
    ingest_id = str(uuid.uuid4())
    ref_id = str(uuid.uuid4())
    files_seen = 1
    lower = filename.lower()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]

    if not data.strip():
        return IngestReceipt(
            files_seen=1,
            ingested=0,
            quarantined=1,
            reasons=[{"file": filename, "reason": "empty_file"}],
            ingest_id=ingest_id,
            source_ref_id=ref_id,
        )
    if lower.endswith((".xlsx", ".xls")):
        return IngestReceipt(
            files_seen=1,
            ingested=0,
            quarantined=1,
            reasons=[
                {
                    "file": filename,
                    "reason": (
                        "xlsx_pending_triage — use batch ingest triage "
                        "(Excel is source-only; no outbound write)"
                    ),
                }
            ],
            ingest_id=ingest_id,
            source_ref_id=ref_id,
        )
    if not lower.endswith(".csv"):
        return IngestReceipt(
            files_seen=1,
            ingested=0,
            quarantined=1,
            reasons=[{"file": filename, "reason": "unsupported_kind"}],
            ingest_id=ingest_id,
            source_ref_id=ref_id,
        )

    db = ensure_demo_warehouse(path or warehouse_path())
    digest = _sha256(data)
    safe = table_name or _safe_table_stem(filename)
    collision_note: str | None = None
    # Prefer medallion schema bronze.<name>; also keep bronze_<name> alias path via schema
    table_qual = f"bronze.{safe}"
    tmp = db.parent / f"_ingest_{ingest_id}.csv"
    # Normalize newlines so DuckDB dialect sniff succeeds on small files
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    tmp.write_text(text, encoding="utf-8")
    con = connect_file(db)
    try:
        ensure_lake_schemas(con)
        # The registry has to exist before *any* path that renames a table into
        # place. It used to be created only by _claim_table_name, which the xlsx
        # path never reaches because batch ingest always passes table_name — so
        # _record_ingest raised after the rename below and the receipt denied
        # rows that had already landed.
        _ensure_registry(con)
        if table_name is None:
            safe, collision_note = _claim_table_name(
                con, stem=safe, filename=filename, digest=digest
            )
            table_qual = f"bronze.{safe}"
        # Build beside the existing table, then swap. The DROP used to run
        # before the CREATE, so a file that failed to parse left the previous
        # table already destroyed while the receipt reported quarantined=1 —
        # the ingest looked rejected and had in fact deleted something.
        staging = f"_ing_{ingest_id.replace('-', '')[:16]}"
        con.execute(f'DROP TABLE IF EXISTS bronze."{staging}"')
        # Provenance: _src is STRUCT(ref_id, row)[] — joins concatenate arrays
        # DuckDB: 'row' is reserved in struct_pack(:=); use struct literal instead.
        con.execute(
            f"""
            CREATE TABLE bronze."{staging}" AS
            SELECT
              src.*,
              [{{'ref_id': '{ref_id}', 'row': row_number() OVER ()::INTEGER}}] AS _src,
              '{ingest_id}'::VARCHAR AS _ingest_id
            FROM read_csv(
              '{tmp.as_posix()}',
              header := true,
              auto_detect := true,
              delim := ',',
              quote := '\"',
              sample_size := -1
            ) AS src
            """
        )
        # Parse succeeded — only now is it safe to replace the previous table.
        # Swap and record as one transaction. Ensuring the registry exists fixes
        # the reported symptom, but the class is that a step *after* an
        # irreversible rename could still fail, leaving the warehouse in a state
        # the receipt contradicts. Inside a transaction the rename is no longer
        # irreversible, so no later failure can produce a lying receipt.
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f'DROP TABLE IF EXISTS bronze."{safe}"')
            con.execute(f'ALTER TABLE bronze."{staging}" RENAME TO "{safe}"')
            _record_ingest(
                con,
                table_name=safe,
                filename=filename,
                digest=digest,
                ingest_id=ingest_id,
                space_id=space_id,
            )
        except Exception:  # noqa: BLE001
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        # Count what the customer receives (R-0001), not what the parse produced.
        n = scalar_int(con.execute(f'SELECT COUNT(*) FROM bronze."{safe}"').fetchone())
    except Exception as exc:  # noqa: BLE001
        try:
            con.execute(f'DROP TABLE IF EXISTS bronze."{staging}"')
        except Exception:  # noqa: BLE001
            pass
        return IngestReceipt(
            files_seen=files_seen,
            ingested=0,
            quarantined=1,
            reasons=[{"file": filename, "reason": f"parse_error:{exc}"[:200]}],
            ingest_id=ingest_id,
            source_ref_id=ref_id,
        )
    finally:
        con.close()
        tmp.unlink(missing_ok=True)

    return IngestReceipt(
        files_seen=files_seen,
        ingested=n,
        quarantined=0,
        reasons=([{"file": filename, "reason": collision_note}] if collision_note else []),
        ingest_id=ingest_id,
        source_ref_id=ref_id,
        table=table_qual,
    )


class _NotText(Exception):
    """A non-str value: DuckDB's VARCHAR cast, not str(), must decide its text."""


def _csv_field(value: Any) -> str:
    """NULL is an empty unquoted field; every value is quoted, so '' stays ''."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _NotText
    return '"' + value.replace('"', '""') + '"'


def _load_raw_rows(
    con: duckdb.DuckDBPyConnection, columns: list[str], rows: list[list[Any]]
) -> None:
    """Bulk-load ``rows`` into the ``_bronze_raw`` temp table.

    ``executemany`` binds one row per call: about 550 rows/s here, so a 1,056,320
    row table spent ~30 minutes in this loop alone and a ~1 GB SQL-source ingest
    took ~2 h in one request. Writing a temp CSV and letting DuckDB scan it lands
    200,000 rows in well under a second.

    Same bytes land either way. NULL vs empty string survives because every
    non-NULL field is quoted and ``allow_quoted_nulls=false``; every column is read
    as VARCHAR, which is what the INSERT produced. Anything the fast path is not
    sure of - a non-str value (the connector only passes str/None), a ragged row,
    a byte DuckDB's CSV reader rejects - falls back to the old per-row INSERT, so
    this can make ingest faster but never lossier or differently typed.
    """
    width = len(columns)
    fd, tmp = tempfile.mkstemp(prefix="dms_bronze_", suffix=".csv")
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                for row in rows:
                    fh.write(",".join(_csv_field(v) for v in row))
                    fh.write("\n")
            spec = "{" + ", ".join(f"'f{i}': 'VARCHAR'" for i in range(width)) + "}"
            con.execute(
                "INSERT INTO _bronze_raw SELECT * FROM read_csv(?, header=false, "
                "delim=',', quote='\"', escape='\"', allow_quoted_nulls=false, "
                f"auto_detect=false, strict_mode=true, columns={spec})",
                [tmp],
            )
            return
        except (_NotText, UnicodeEncodeError, duckdb.Error):
            con.execute("DELETE FROM _bronze_raw")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    con.executemany(
        f"INSERT INTO _bronze_raw VALUES ({', '.join(['?'] * width)})",
        rows,
    )


def write_bronze_rows(
    *,
    table: str,
    columns: list[str],
    rows: list[list[Any]],
    ref_id: str | None = None,
    ingest_id: str | None = None,
    path: Path | None = None,
) -> str:
    """Test/helper: write rows into bronze.<table> with provenance."""
    ingest_id = ingest_id or str(uuid.uuid4())
    ref_id = ref_id or str(uuid.uuid4())
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "bronze", table
    if not columns:
        raise ValueError("columns required")
    db = ensure_demo_warehouse(path or warehouse_path())
    con = connect_file(db)
    try:
        ensure_lake_schemas(con)
        con.execute(f'DROP TABLE IF EXISTS "{schema}"."{name}"')
        col_defs = ", ".join(f'"{c}" VARCHAR' for c in columns)
        con.execute(f'CREATE TEMP TABLE _bronze_raw ({col_defs})')
        if rows:
            _load_raw_rows(con, columns, rows)
        con.execute(
            f"""
            CREATE TABLE "{schema}"."{name}" AS
            SELECT
              src.*,
              [{{'ref_id': '{ref_id}', 'row': row_number() OVER ()::INTEGER}}] AS _src,
              '{ingest_id}'::VARCHAR AS _ingest_id
            FROM _bronze_raw AS src
            """
        )
        return f"{schema}.{name}"
    finally:
        con.close()


#: Source DATA_TYPE (lower-cased) -> DuckDB type. Declared types only; anything
#: not listed stays VARCHAR and is named on the receipt.
_INT_TYPES = frozenset(
    {
        "int",
        "integer",
        "int2",
        "int4",
        "int8",
        "smallint",
        "bigint",
        "tinyint",
        "mediumint",
        "serial",
        "bigserial",
        "smallserial",
    }
)
_FLOAT_TYPES = frozenset({"real", "float", "float4", "float8", "double", "double precision"})
_TEXT_TYPES = frozenset(
    {
        "varchar",
        "character varying",
        "text",
        "char",
        "character",
        "nvarchar",
        "nchar",
        "ntext",
        "bpchar",
        "citext",
        "uuid",
        "uniqueidentifier",
        "longtext",
        "mediumtext",
        "tinytext",
        "enum",
    }
)
_DECIMAL_TYPES = frozenset({"numeric", "decimal", "money", "smallmoney", "number"})
_SIMPLE_TYPES = {
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "bit": "BOOLEAN",
    "date": "DATE",
    "time": "TIME",
    "time without time zone": "TIME",
    "timestamp": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP",
}
#: Kept VARCHAR on purpose (adversary round 4): the driver writes these with the
#: source session's offset (``05:00+08:00``) and a DuckDB TIMESTAMPTZ reads them in
#: the server zone, so a day bucket moves (2024-01-01 becomes 2023-12-31). The text
#: keeps the source's own clock, which is what the pre-typing answers used.
_ZONED_TYPES = frozenset({"timestamptz", "timestamp with time zone", "datetimeoffset"})


def duckdb_type_for(data_type: str, precision: int | None, scale: int | None) -> str | None:
    """The DuckDB type a declared source type lands as, or None to stay VARCHAR."""
    t = " ".join(str(data_type).lower().split())
    if t in _TEXT_TYPES:
        return "VARCHAR"
    if t in _INT_TYPES:
        return "BIGINT"
    if t in _FLOAT_TYPES:
        return "DOUBLE"
    if t in {"money", "smallmoney"}:
        return "DECIMAL(19,4)"
    if t in _DECIMAL_TYPES:
        # DuckDB DECIMAL holds 38 digits. Wider, or undeclared precision
        # (Postgres bare NUMERIC), cannot be held exactly: stays VARCHAR.
        if precision is None or not 1 <= precision <= 38:
            return None
        s = scale or 0
        return f"DECIMAL({precision},{s})" if 0 <= s <= precision else None
    return _SIMPLE_TYPES.get(t)


_NUMERIC_TYPES = _INT_TYPES | _FLOAT_TYPES | _DECIMAL_TYPES


def _bare_decimal(con: Any, rel: str, q: str) -> str | None:
    """DECIMAL(38, s) for an undeclared-precision numeric, or None.

    ``s`` is the largest scale the values carry. Plain decimal notation only (an
    exponent is refused, not reinterpreted); the exactness check in the caller
    still has to pass before anything is converted.
    """
    row = con.execute(
        f"SELECT COUNT(*) FILTER (WHERE NOT regexp_full_match(trim({q}), '-?[0-9]+(\\.[0-9]+)?')), "
        f"COALESCE(MAX(length(split_part(trim({q}), '.', 2))), 0), "
        f"COALESCE(MAX(length(ltrim(split_part(trim({q}), '.', 1), '-'))), 0) "
        f"FROM {rel} WHERE {q} IS NOT NULL"
    ).fetchone()
    if row is None or int(row[0]):
        return None
    scale, whole = int(row[1]), int(row[2])
    if whole + scale > 38 or scale > 38:
        return None
    return f"DECIMAL(38,{scale})"


def type_bronze_columns(
    *,
    table: str,
    declared: dict[str, tuple[str, int | None, int | None]] | None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Give landed bronze columns their source-declared types (dms#277 F-e).

    Rows land as VARCHAR (``write_bronze_rows``), which made every generated
    ``SUM``/``AVG`` over a numeric SQL-source column fail validation: the
    largest known BIRD cost. A column with a mapped declared type is converted
    only if EVERY non-NULL value converts AND, for a numeric target, converts
    to the same number (DuckDB rounds ``'1.5'`` to an integer 2 instead of
    failing). Otherwise it stays VARCHAR and is named, never half-converted.

    ``untyped_numeric`` lists columns declared numeric that stayed VARCHAR; the
    ask path refuses SQL that reads them, because text compares ``'9.50' >
    '100.25'``.
    """
    column_types: dict[str, str] = {}
    untyped: dict[str, str] = {}
    untyped_numeric: list[str] = []
    schema, _, name = table.partition(".")
    rel = f'"{schema}"."{name}"'
    db = ensure_demo_warehouse(path or warehouse_path())
    con = connect_file(db)
    try:
        cols = [
            str(r[0])
            for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
            if str(r[0]) not in {"_src", "_ingest_id"}
        ]
        for col in cols:
            column_types[col] = "VARCHAR"
            if declared is None:
                untyped[col] = "source did not declare column types"
                continue
            spec = declared.get(col)
            if spec is None:
                untyped[col] = "type not declared"
                continue
            kind = " ".join(str(spec[0]).lower().split())
            numeric = kind in _NUMERIC_TYPES
            q = '"' + col.replace('"', '""') + '"'
            if kind in _ZONED_TYPES:
                untyped[col] = (
                    f"declared {spec[0]!r} kept as source text: a zoned timestamp "
                    "read in the server's zone would move day boundaries"
                )
                continue
            target = duckdb_type_for(*spec)
            if target is None and kind in _DECIMAL_TYPES and spec[1] is None:
                target = _bare_decimal(con, rel, q)
            if target is None:
                untyped[col] = f"declared type {spec[0]!r} has no exact DuckDB type"
                if numeric or kind in {"money", "smallmoney"}:
                    untyped_numeric.append(col)
                continue
            if target == "VARCHAR":
                continue
            exact = (
                f" OR TRY_CAST(TRY_CAST({q} AS {target}) AS DECIMAL(38,18)) "
                f"IS DISTINCT FROM TRY_CAST({q} AS DECIMAL(38,18))"
                if target == "BIGINT" or target.startswith("DECIMAL")
                else ""
            )
            bad = con.execute(
                f"SELECT COUNT(*) FROM {rel} WHERE {q} IS NOT NULL "
                f"AND (TRY_CAST({q} AS {target}) IS NULL{exact})"
            ).fetchone()
            if bad is None or int(bad[0]):
                untyped[col] = (
                    f"{int(bad[0]) if bad else '?'} value(s) do not fit declared {target} exactly"
                )
                if numeric or kind in {"money", "smallmoney"}:
                    untyped_numeric.append(col)
                continue
            con.execute(f"ALTER TABLE {rel} ALTER {q} TYPE {target} USING CAST({q} AS {target})")
            column_types[col] = target
    finally:
        con.close()
    return {
        "column_types": column_types,
        "untyped_columns": untyped,
        "untyped_numeric": untyped_numeric,
    }


def untyped_numeric_columns(tables: set[str], *, path: Path | None = None) -> dict[str, set[str]]:
    """``bronze.<t>`` -> declared-numeric columns that landed VARCHAR, for ``tables``.

    Raises when the registry cannot be read; the caller refuses rather than
    answering over columns it cannot vouch for.
    """
    bare = {t.split(".", 1)[-1].lower(): t for t in tables if t.lower().startswith("bronze.")}
    if not bare:
        return {}
    # Read-only, never seeding the registry: "no warehouse" / "no registry" read
    # as nothing flagged; an unreadable one raises WarehouseBusy.
    rows = _registry_rows(
        lambda col: (
            f"SELECT r.table_name, {col('untyped_numeric')} FROM {_REGISTRY} r "
            f"WHERE {col('untyped_numeric')} IS NOT NULL"
        ),
        [],
        path=path,
    )
    out: dict[str, set[str]] = {}
    for name, cols in rows:
        key = str(name).lower()
        if key in bare and cols:
            out[f"bronze.{key}"] = {c.lower() for c in str(cols).split(",") if c}
    return out


def list_bronze_tables(
    *,
    path: Path | None = None,
    space_id: str | None = None,
) -> list[dict[str, Any]]:
    from dms_executor.demo_grants import canonical_space_id

    canon_space = canonical_space_id(space_id) if space_id else None
    # One write-mode attach for ensure + list. DuckDB 1.5 unique-file-handle
    # 500s a second RW attach of the same file; connect_readonly serializes.
    # Mixed read_only=True vs RW also 500s (Library fires /tree twice).
    con = connect_readonly(path)
    try:
        ensure_lake_schemas(con)
        _ensure_registry(con)
        # Internal bookkeeping tables are named with a leading underscore and
        # must not reach the file picker — _ingest_registry used to exist only
        # after a CSV ingest, and now that it is created up front it would
        # otherwise appear as a tickable "file" in Studio.
        if canon_space:
            rows = con.execute(
                f"""
                SELECT t.table_schema, t.table_name, r.space_id
                  FROM information_schema.tables t
                  INNER JOIN {_REGISTRY} r ON r.table_name = t.table_name
                 WHERE ((t.table_schema = 'bronze')
                     OR (t.table_schema = 'main' AND t.table_name LIKE 'bronze_%'))
                   AND t.table_name NOT LIKE '\\_%' ESCAPE '\\'
                   AND r.space_id = ?
                 ORDER BY t.table_schema, t.table_name
                """,
                [canon_space],
            ).fetchall()
        else:
            rows = [
                (*row, None)
                for row in con.execute(
                    """
                    SELECT table_schema, table_name FROM information_schema.tables
                    WHERE ((table_schema = 'bronze')
                        OR (table_schema = 'main' AND table_name LIKE 'bronze_%'))
                      AND table_name NOT LIKE '\\_%' ESCAPE '\\'
                    ORDER BY table_schema, table_name
                    """
                ).fetchall()
            ]
        watermarks: dict[str, tuple[Any, ...]] = {}
        try:
            for reg in con.execute(
                f"SELECT table_name, filename, extracted_at, truncated, source_kind "
                f"FROM {_REGISTRY}"
            ).fetchall():
                watermarks[str(reg[0])] = reg
        except Exception:  # noqa: BLE001 - old warehouse without the columns
            watermarks = {}
        out = []
        for schema, name, row_space in rows:
            cnt = scalar_int(
                con.execute(f'SELECT COUNT(*) FROM "{schema}"."{name}"').fetchone()
            )
            label = f"{schema}.{name}" if schema != "main" else name
            entry: dict[str, Any] = {"table": label, "row_count": cnt}
            if row_space:
                entry["space_id"] = row_space
            wm = watermarks.get(name)
            if wm is not None:
                filename, extracted_at, truncated, source_kind = wm[1], wm[2], wm[3], wm[4]
                entry["source"] = None if filename is None else str(filename)
                entry["extracted_at"] = None if extracted_at is None else str(extracted_at)
                entry["truncated"] = None if truncated is None else bool(truncated)
                entry["source_kind"] = source_kind or classify_source_kind(
                    None if filename is None else str(filename)
                )
            else:
                entry["source"] = None
                entry["extracted_at"] = None
                entry["truncated"] = None
                entry["source_kind"] = None
            out.append(entry)
        return out
    finally:
        con.close()
