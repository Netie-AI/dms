"""Bronze writer — every row gets _src STRUCT[] + _ingest_id (Appendix A)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
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
) -> None:
    con.execute(f"DELETE FROM {_REGISTRY} WHERE table_name = ?", [table_name])
    con.execute(
        f"INSERT INTO {_REGISTRY} VALUES (?, ?, ?, ?, now())",
        [table_name, filename, digest, ingest_id],
    )


def ingest_csv_bytes(
    *,
    filename: str,
    data: bytes,
    path: Path | None = None,
    table_name: str | None = None,
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
                    "reason": "xlsx_pending_triage — use batch ingest triage (Excel is source-only; no outbound write)",
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
        n = int(con.execute(f'SELECT COUNT(*) FROM bronze."{staging}"').fetchone()[0])
        # Parse succeeded — only now is it safe to replace the previous table.
        con.execute(f'DROP TABLE IF EXISTS bronze."{safe}"')
        con.execute(f'ALTER TABLE bronze."{staging}" RENAME TO "{safe}"')
        _record_ingest(
            con, table_name=safe, filename=filename, digest=digest, ingest_id=ingest_id
        )
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


def list_bronze_tables(*, path: Path | None = None) -> list[dict[str, Any]]:
    db = ensure_demo_warehouse(path or warehouse_path())
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE (table_schema = 'bronze')
               OR (table_schema = 'main' AND table_name LIKE 'bronze_%')
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        out = []
        for schema, name in rows:
            cnt = int(
                con.execute(f'SELECT COUNT(*) FROM "{schema}"."{name}"').fetchone()[0]
            )
            label = f"{schema}.{name}" if schema != "main" else name
            out.append({"table": label, "row_count": cnt})
        return out
    finally:
        con.close()
