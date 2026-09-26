"""Bronze writer — every row gets _src STRUCT[] + _ingest_id (Appendix A)."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_warehouse import (
    connect_file,
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


def _safe_table_stem(filename: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in Path(filename).stem)[:40]


def bronze_table_for_sheet(filename: str, sheet: str | None = None) -> str:
    """Ident ingest writes: ``{stem}_{sheet}``, alnum/underscore, 40 chars, ``bronze.`` prefix."""
    stem = Path(filename).stem
    if sheet:
        stem = f"{stem}_{sheet}"
    ident = "".join(c if c.isalnum() else "_" for c in stem)[:40]
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


def _claim_table_name(
    con: duckdb.DuckDBPyConnection, *, stem: str, filename: str, digest: str
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
    row = con.execute(
        f"SELECT filename FROM {_REGISTRY} WHERE table_name = ?", [stem]
    ).fetchone()
    if row is None or row[0] == filename:
        return stem, None
    suffix = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]
    return (
        f"{stem[:31]}_{suffix}",
        f"name {stem!r} already holds {row[0]!r}; stored separately",
    )


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
    *, stem: str, source: str, path: Path | None = None
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
    con = duckdb.connect(str(db))
    try:
        ensure_lake_schemas(con)
        _ensure_registry(con)
        return _claim_table_name(con, stem=stem, filename=source, digest="")
    finally:
        con.close()


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
    con = duckdb.connect(str(db))
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
    finally:
        con.close()
    return fingerprint


def lookup_ingest_watermarks(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """One read-only pass over the registry. Does not seed a warehouse (P-DMS-34)."""
    db = path or warehouse_path()
    if not Path(db).is_file():
        return {}
    con = duckdb.connect(str(db))
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


def _registry_rows(sql: str, params: list[Any], *, path: Path | None) -> list[tuple[Any, ...]]:
    """Read the ingest registry without seeding a warehouse. Missing file/column -> []."""
    db = Path(path or warehouse_path())
    if not db.is_file():
        return []
    con = connect_file(db)
    try:
        return [tuple(r) for r in con.execute(sql, params).fetchall()]
    except duckdb.Error:
        # No registry yet, or an old one without the widened columns.
        return []
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
        f"""
        SELECT r.table_name, r.filename, r.space_id, r.row_count, r.truncated,
               r.source_row_count, r.extracted_at, r.ingest_id
          FROM {_REGISTRY} r
          JOIN information_schema.tables t
            ON t.table_schema = 'bronze' AND t.table_name = r.table_name
         WHERE r.source_kind = 'sql'
         ORDER BY r.table_name
        """,
        [],
        path=path,
    )
    want = canonical_space_id(space_id) if space_id else None
    out: list[dict[str, Any]] = []
    for name, filename, row_space, row_count, truncated, total, extracted_at, ingest_id in rows:
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


def truncation_notes(
    *,
    tables: list[str],
    sql: str | None = None,
    path: Path | None = None,
) -> list[str]:
    """Assumption lines for every capped bronze table an answer read.

    A table landed under the row cap answers over the rows that landed, not the
    source. Saying nothing is the silent-fallback lie: a total over 500,000 of
    1,056,320 rows reads as the whole ledger. Matching is on the bare bronze name
    in ``tables`` (sources / grounded tables) or as an identifier in ``sql``.
    """
    rows = _registry_rows(
        f"SELECT table_name, row_count, source_row_count FROM {_REGISTRY} "
        "WHERE truncated",
        [],
        path=path,
    )
    if not rows:
        return []
    bare = set()
    for t in tables:
        label = str(t or "").strip().strip('"')
        for prefix in ("bronze:", "bronze."):
            label = label.removeprefix(prefix)
        if label:
            bare.add(label.rsplit(".", 1)[-1].strip('"').lower())
    sql_l = (sql or "").lower()
    notes: list[str] = []
    for name, row_count, total in rows:
        key = str(name).lower()
        hit = key in bare or (
            bool(sql_l)
            and re.search(rf'(?<![\w$]){re.escape(key)}(?![\w$])', sql_l) is not None
        )
        if not hit:
            continue
        loaded = "?" if row_count is None else f"{int(row_count):,}"
        of = "an unknown number of" if total is None else f"{int(total):,}"
        notes.append(
            f"partial table: bronze.{name} holds {loaded} of {of} source rows "
            "(ingest row cap); this answer covers the loaded rows only"
        )
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
    con = duckdb.connect(str(db))
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
    con = duckdb.connect(str(db))
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
