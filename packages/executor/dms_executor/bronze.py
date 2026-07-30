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
    safe = table_name or _safe_table_stem(filename)
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
        con.execute(f'DROP TABLE IF EXISTS bronze."{safe}"')
        # Provenance: _src is STRUCT(ref_id, row)[] — joins concatenate arrays
        # DuckDB: 'row' is reserved in struct_pack(:=); use struct literal instead.
        con.execute(
            f"""
            CREATE TABLE bronze."{safe}" AS
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
        n = int(con.execute(f'SELECT COUNT(*) FROM bronze."{safe}"').fetchone()[0])
    except Exception as exc:  # noqa: BLE001
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

    _ = _sha256(data)
    return IngestReceipt(
        files_seen=files_seen,
        ingested=n,
        quarantined=0,
        reasons=[],
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
