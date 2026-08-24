"""Batch ingest with triage — honest receipts; UNSTRUCTURED never becomes a table."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from dms_core.triage import FileTriageResult, SheetClass, TriageReceipt

from dms_executor.bronze import ingest_csv_bytes
from dms_executor.demo_warehouse import ensure_demo_warehouse, warehouse_path
from dms_executor.document_chunks import index_unstructured_upload
from dms_executor.triage import classify_bytes, parse_csv_grid
from dms_executor.warehouse_identity import maybe_sync_bronze_to_serving

logger = logging.getLogger(__name__)


def _blob_put(key: str, data: bytes, *, root: Path) -> str:
    """Local object-store stub — swap scenario: local FS → MinIO → S3 (ObjectStorePort)."""
    dest = root / "blobs" / "sha256" / key[:2] / key[2:4] / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return str(dest.as_posix())


def _grid_to_csv_bytes(grid: list[list[Any]], header_row: int) -> bytes:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    # Use header row and subsequent non-blank rows until a sparse notes row
    if header_row >= len(grid):
        return b""
    header = grid[header_row]
    w.writerow([("" if c is None else str(c)) for c in header])
    for row in grid[header_row + 1 :]:
        cells = [("" if c is None else str(c)).strip() for c in row]
        if all(not c for c in cells):
            break
        # stop at long single-cell notes
        nonempty = [c for c in cells if c]
        if len(nonempty) == 1 and len(nonempty[0]) > 40:
            break
        w.writerow(cells)
    return buf.getvalue().encode("utf-8")


def ingest_batch(
    files: list[tuple[str, bytes]],
    *,
    path: Path | None = None,
    space_id: str | None = None,
    database_url: str | None = None,
    tenant_id: str | None = None,
) -> TriageReceipt:
    """Classify every file/sheet; ingest clean/dirty tabular; blob unstructured.

    Never hard-fails the whole batch — each file gets a named reason.
    UNSTRUCTURED with ``space_id`` + Postgres writes space-scoped chunks (RAG-01);
    without either, ``document_index`` stays ``pending`` — never a silent table.
    """
    ingest_id = str(uuid.uuid4())
    db_path = ensure_demo_warehouse(path or warehouse_path())
    blob_root = db_path.parent
    results: list[FileTriageResult] = []
    per_class: dict[str, int] = {}
    ingested_count = 0
    need_attention = 0
    conninfo = database_url if database_url is not None else os.environ.get("DATABASE_URL")
    tid = tenant_id or os.environ.get(
        "DMS_TENANT_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    for filename, data in files:
        triages = classify_bytes(filename=filename, data=data)
        for tr in triages:
            per_class[tr.classification.value] = per_class.get(tr.classification.value, 0) + 1
            if tr.classification == SheetClass.UNSTRUCTURED:
                key = hashlib.sha256(data).hexdigest()
                blob_key = _blob_put(key, data, root=blob_root)
                tr.blob_key = blob_key
                tr.ingested = False
                tr.document_index = "pending"
                if space_id and conninfo:
                    try:
                        indexed = index_unstructured_upload(
                            filename=filename,
                            data=data,
                            space_id=space_id,
                            blob_key=blob_key,
                            database_url=conninfo,
                            tenant_id=tid,
                        )
                        tr.document_index = indexed["document_index"]
                        tr.chunk_count = int(indexed["chunk_count"])
                        tr.source_id = str(indexed["source_id"])
                    except Exception as exc:  # noqa: BLE001
                        tr.reason = f"{tr.reason}+chunk_index_failed:{exc}"[:180]
                        need_attention += 1
                else:
                    if not space_id:
                        tr.reason = f"{tr.reason}+awaiting_space_id"
                    elif not conninfo:
                        tr.reason = f"{tr.reason}+awaiting_database_url"
                    need_attention += 1
                results.append(tr)
                continue

            if tr.classification in (SheetClass.MULTI_TABLE, SheetClass.HEADERLESS):
                tr.ingested = False
                need_attention += 1
                results.append(tr)
                continue

            # TABULAR_CLEAN or TABULAR_DIRTY — attempt bronze write
            # Dirty still lands (honest receipt names the fix) so steward can repair later
            try:
                if filename.lower().endswith((".xlsx", ".xlsm")):
                    from dms_executor.triage import parse_xlsx_grids

                    sheets = parse_xlsx_grids(data)
                    sheet_grid = None
                    for name, g in sheets:
                        if tr.sheet is None or name == tr.sheet:
                            sheet_grid = g
                            break
                    if sheet_grid is None or tr.header_row is None:
                        tr.ingested = False
                        tr.reason = tr.reason + "+no_extractable_grid"
                        need_attention += 1
                        results.append(tr)
                        continue
                    csv_bytes = _grid_to_csv_bytes(sheet_grid, tr.header_row)
                    stem = f"{Path(filename).stem}_{tr.sheet or 'sheet'}"
                    receipt = ingest_csv_bytes(
                        filename=f"{stem}.csv",
                        data=csv_bytes,
                        path=db_path,
                        table_name="".join(c if c.isalnum() else "_" for c in stem)[:40],
                        space_id=space_id,
                    )
                else:
                    # CSV: if dirty with title row, re-slice from header_row
                    if tr.header_row and tr.header_row > 0:
                        grid = parse_csv_grid(data)
                        csv_bytes = _grid_to_csv_bytes(grid, tr.header_row)
                        receipt = ingest_csv_bytes(
                            filename=filename,
                            data=csv_bytes,
                            path=db_path,
                            space_id=space_id,
                        )
                    else:
                        receipt = ingest_csv_bytes(
                            filename=filename,
                            data=data,
                            path=db_path,
                            space_id=space_id,
                        )
                if receipt.quarantined and not receipt.table:
                    tr.ingested = False
                    tr.reason = receipt.reasons[0]["reason"] if receipt.reasons else "ingest_failed"
                    need_attention += 1
                else:
                    tr.ingested = True
                    tr.table = receipt.table
                    ingested_count += 1
                    if tr.classification == SheetClass.TABULAR_DIRTY:
                        need_attention += 1  # ingested but needs attention
            except Exception as exc:  # noqa: BLE001
                tr.ingested = False
                tr.reason = f"ingest_error:{exc}"[:180]
                need_attention += 1
            results.append(tr)

    if ingested_count:
        synced = maybe_sync_bronze_to_serving(db_path)
        if synced is not None and not synced.ok:
            logger.warning(
                "bronze landed in %s but chat serving %s was not updated (%s): %s",
                synced.ingest,
                synced.serving,
                synced.status,
                synced.error or "run python scripts/sync_bronze_to_serving.py",
            )

    return TriageReceipt(
        files_seen=len(files),
        ingested=ingested_count,
        need_attention=need_attention,
        per_class=per_class,
        files=results,
        ingest_id=ingest_id,
    )
