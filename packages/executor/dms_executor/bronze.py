"""Bronze writer — every row gets _src STRUCT[] + _ingest_id (Appendix A)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
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


def _ensure_registry(con: duckdb.DuckDBPyConnection) -> None:
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
) -> None:
    con.execute(f"DELETE FROM {_REGISTRY} WHERE table_name = ?", [table_name])
    # Named columns, not positional VALUES. The registry has been widened three
    # times; a positional insert silently misaligns the moment a column is added.
    con.execute(
        f"INSERT INTO {_REGISTRY} "
        "(table_name, filename, sha256, ingest_id, created_at, space_id, row_count, truncated) "
        "VALUES (?, ?, ?, ?, now(), ?, ?, ?)",
        [table_name, filename, digest, ingest_id, space_id, row_count, truncated],
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
) -> str:
    """Name the SQL source a bronze table was pulled from (DR-0005 part 4).

    The registry was built for files: ``filename`` and a content ``sha256``. A SQL pull
    has neither, and the parked connector wrote none of this, so a SQL-sourced table
    carried row provenance (``_src``) and no source provenance - half an answer.

    Rather than widen a table other lanes read, the file columns carry the SQL
    equivalents. ``filename`` holds the credential-free source string
    (``sqlserver://host:port/db#schema.table``). ``sha256`` holds a fingerprint of the
    pull - source, row count, truncation - so a re-pull that landed a different number
    of rows is detectable as a different ingest rather than silently the same one.

    Lives here, not in the connector, because the connector must never hold a DuckDB
    handle: extract-only is asserted on its source text
    (``tests/invariants/test_extract_only.py``).
    """
    fingerprint = hashlib.sha256(
        f"{source}|rows={row_count}|truncated={truncated}".encode()
    ).hexdigest()
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
        )
    finally:
        con.close()
    return fingerprint


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
            con.executemany(
                f"INSERT INTO _bronze_raw VALUES ({', '.join(['?'] * len(columns))})",
                rows,
            )
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

    db = ensure_demo_warehouse(path or warehouse_path())
    canon_space = canonical_space_id(space_id) if space_id else None
    if canon_space:
        init = duckdb.connect(str(db))
        try:
            ensure_lake_schemas(init)
            _ensure_registry(init)
        finally:
            init.close()
    con = duckdb.connect(str(db), read_only=True)
    try:
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
        out = []
        for schema, name, row_space in rows:
            cnt = scalar_int(
                con.execute(f'SELECT COUNT(*) FROM "{schema}"."{name}"').fetchone()
            )
            label = f"{schema}.{name}" if schema != "main" else name
            entry: dict[str, Any] = {"table": label, "row_count": cnt}
            if row_space:
                entry["space_id"] = row_space
            out.append(entry)
        return out
    finally:
        con.close()
