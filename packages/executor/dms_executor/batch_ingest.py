"""Batch ingest with triage — honest receipts; UNSTRUCTURED never becomes a table.

Each data row's land decision is appended to ``bronze._land_row_log``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from dms_core.triage import FileTriageResult, SheetClass, TriageReceipt

from dms_executor.bronze import bronze_table_for_sheet, ingest_csv_bytes
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse, warehouse_path
from dms_executor.document_chunks import index_unstructured_upload
from dms_executor.lake_schema import ensure_lake_schemas
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


def _row_cells(row: list[Any]) -> list[str]:
    return [("" if c is None else str(c)).strip() for c in row]


def _blank_row(cells: list[str]) -> bool:
    return all(not c for c in cells)


def _numericish(text: str) -> bool:
    t = text.replace(",", "").replace(" ", "")
    if t.startswith("="):
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


_HEADERISH = re.compile(
    r"^(sku|category|region|month|revenue|units|id|name|date|qty|quantity|"
    r"sales|city|product|status|amount|value|code|type)",
    re.I,
)


def _looks_like_header(cells: list[str]) -> bool:
    nonempty = [c for c in cells if c]
    if len(nonempty) < 2 or any(_numericish(c) for c in nonempty):
        return False
    named = sum(1 for c in nonempty if _HEADERISH.match(c) or "_" in c)
    return named >= 2


# id, status, reason, cells (set only for an accepted data row).
_Fate = tuple[int, str, str, list[str] | None]

#: Leading underscore keeps this out of list_bronze_tables / the file picker.
# ponytail: one append-only table on the ingest file. Ceiling: it grows with
# every batch for the life of that file. Upgrade: drop by ingest_id if a
# warehouse is kept across months of uploads.
_LAND_ROW_LOG = "bronze._land_row_log"


def _source_label(filename: str, sheet: str | None) -> str:
    if sheet:
        return f"{filename}#{sheet}"
    return filename


def _trim_before_land(classification: SheetClass, header_row: int | None) -> bool:
    """The CSV branch already trims when this is true. The log uses the same rule."""
    return classification == SheetClass.MULTI_TABLE or bool(header_row and header_row > 0)


def _grid_fates(grid: list[list[Any]], header_row: int) -> list[_Fate]:
    """Row fates for the trim path. Accepted cells are the CSV body, in order.

    Stop rules match the land trim: blank rows, trailing notes, totals, a
    repeated header, and a second table are not written. The header row itself
    is the schema line, not a data row.
    """
    if header_row >= len(grid):
        if not grid:
            return [(1, "failed", "no_extractable_grid", None)]
        return [(i + 1, "failed", "no_extractable_grid", None) for i in range(len(grid))]
    rows = [_row_cells(r) for r in grid]
    header_key = tuple(c.lower() for c in rows[header_row])
    fates: list[_Fate] = [
        (i + 1, "dropped", "above_header", None) for i in range(header_row)
    ]
    i = header_row + 1
    while i < len(rows):
        cells = rows[i]
        if _blank_row(cells):
            fates.append((i + 1, "dropped", "blank_row", None))
            i += 1
            continue
        nonempty = [c for c in cells if c]
        if len(nonempty) == 1 and len(nonempty[0]) > 40:
            fates.extend(
                (k + 1, "dropped", "trailing_note", None) for k in range(i, len(rows))
            )
            break
        if nonempty and nonempty[0].lower() in {"total", "grand total", "sum"}:
            fates.extend(
                (k + 1, "dropped", "total_row", None) for k in range(i, len(rows))
            )
            break
        row_key = tuple(c.lower() for c in cells)
        if row_key == header_key:
            fates.append((i + 1, "dropped", "duplicate_header", None))
            i += 1
            continue
        if _looks_like_header(cells) and row_key != header_key:
            fates.extend(
                (k + 1, "dropped", "second_table", None) for k in range(i, len(rows))
            )
            break
        if len(nonempty) == 1:
            j = i + 1
            while j < len(rows) and _blank_row(rows[j]):
                j += 1
            if j < len(rows) and _looks_like_header(rows[j]):
                nxt = tuple(c.lower() for c in rows[j])
                if nxt != header_key:
                    fates.extend(
                        (k + 1, "dropped", "section_break", None)
                        for k in range(i, len(rows))
                    )
                    break
        fates.append((i + 1, "accepted", "landed", cells))
        i += 1
    return fates


def _whole_file_fates(grid: list[list[Any]]) -> list[_Fate]:
    """Data rows when the original bytes are landed. Row 1 is the header."""
    return [(i + 1, "accepted", "landed", None) for i in range(1, len(grid))]


def _refused_fates(
    grid: list[list[Any]] | None, *, status: str, reason: str
) -> list[_Fate]:
    why = reason or "unspecified"
    if not grid:
        return [(1, status, why, None)]
    return [(i + 1, status, why, None) for i in range(len(grid))]


def _fail_accepted(fates: list[_Fate], reason: str) -> list[_Fate]:
    """Rows the trim kept, but the write did not land, are failed."""
    if not fates:
        return _refused_fates(None, status="failed", reason=reason)
    why = reason or "ingest_failed"
    out: list[_Fate] = []
    for row_id, status, prev, _cells in fates:
        if status == "accepted":
            out.append((row_id, "failed", why, None))
        else:
            out.append((row_id, status, prev, None))
    return out


def _load_grid(filename: str, data: bytes, sheet: str | None) -> list[list[Any]] | None:
    lower = filename.lower()
    try:
        if lower.endswith((".xlsx", ".xlsm")):
            from dms_executor.triage import parse_xlsx_grids

            for name, grid in parse_xlsx_grids(data):
                if sheet is None or name == sheet:
                    return grid
            return None
        if lower.endswith(".xls"):
            return None
        return parse_csv_grid(data)
    except Exception:  # noqa: BLE001 - unreadable bytes still get one log row
        return None


def _remember(
    log: list[tuple[int, str, str, str]], source: str, fates: list[_Fate]
) -> None:
    for row_id, status, reason, _cells in fates:
        log.append((row_id, source, status, reason))


def _write_land_row_log(
    path: Path, ingest_id: str, rows: list[tuple[int, str, str, str]]
) -> None:
    con = connect_file(path, write=True)
    try:
        ensure_lake_schemas(con)
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_LAND_ROW_LOG} (
              ingest_id VARCHAR,
              id INTEGER,
              source VARCHAR,
              status VARCHAR,
              reason VARCHAR
            )
            """
        )
        if not rows:
            return
        con.executemany(
            f"INSERT INTO {_LAND_ROW_LOG} (ingest_id, id, source, status, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (ingest_id, row_id, source, status, reason)
                for row_id, source, status, reason in rows
            ],
        )
    finally:
        con.close()


def _grid_to_csv_bytes(grid: list[list[Any]], header_row: int) -> bytes:
    import csv
    import io

    if header_row >= len(grid):
        return b""
    rows = [_row_cells(r) for r in grid]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(rows[header_row])
    for _row_id, status, _reason, cells in _grid_fates(grid, header_row):
        if status == "accepted" and cells is not None:
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
    log_rows: list[tuple[int, str, str, str]] = []
    conninfo = database_url if database_url is not None else os.environ.get("DATABASE_URL")
    tid = tenant_id or os.environ.get(
        "DMS_TENANT_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    for filename, data in files:
        triages = classify_bytes(filename=filename, data=data)
        for tr in triages:
            per_class[tr.classification.value] = per_class.get(tr.classification.value, 0) + 1
            source = _source_label(filename, tr.sheet)
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
                _remember(
                    log_rows,
                    source,
                    _refused_fates(
                        _load_grid(filename, data, tr.sheet),
                        status="dropped",
                        reason=tr.reason,
                    ),
                )
                results.append(tr)
                continue

            if tr.classification == SheetClass.HEADERLESS:
                tr.ingested = False
                need_attention += 1
                _remember(
                    log_rows,
                    source,
                    _refused_fates(
                        _load_grid(filename, data, tr.sheet),
                        status="failed",
                        reason=tr.reason,
                    ),
                )
                results.append(tr)
                continue

            # TABULAR_CLEAN / TABULAR_DIRTY / MULTI_TABLE — attempt bronze write.
            # MULTI_TABLE still needs attention (split remaining regions), but the
            # first header band is what a uniquely scoped sheet ask can certify.
            # Dirty still lands (honest receipt names the fix) so steward can repair later
            fates: list[_Fate] = []
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
                        _remember(
                            log_rows,
                            source,
                            _refused_fates(
                                sheet_grid, status="failed", reason=tr.reason
                            ),
                        )
                        results.append(tr)
                        continue
                    fates = _grid_fates(sheet_grid, tr.header_row)
                    csv_bytes = _grid_to_csv_bytes(sheet_grid, tr.header_row)
                    stem = f"{Path(filename).stem}_{tr.sheet or 'sheet'}"
                    receipt = ingest_csv_bytes(
                        filename=f"{stem}.csv",
                        data=csv_bytes,
                        path=db_path,
                        table_name=bronze_table_for_sheet(filename, tr.sheet or "sheet").split(
                            ".", 1
                        )[-1],
                        space_id=space_id,
                    )
                else:
                    # CSV: title row, or MULTI_TABLE first band (stop at blank).
                    # Whole-file ingest would union stacked tables — the merge trap.
                    if _trim_before_land(tr.classification, tr.header_row):
                        grid = parse_csv_grid(data)
                        header_row = tr.header_row or 0
                        fates = _grid_fates(grid, header_row)
                        csv_bytes = _grid_to_csv_bytes(grid, header_row)
                        receipt = ingest_csv_bytes(
                            filename=filename,
                            data=csv_bytes,
                            path=db_path,
                            space_id=space_id,
                        )
                    else:
                        fates = _whole_file_fates(parse_csv_grid(data))
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
                    fates = _fail_accepted(fates, tr.reason)
                else:
                    tr.ingested = True
                    tr.table = receipt.table
                    ingested_count += 1
                    if tr.classification in (
                        SheetClass.TABULAR_DIRTY,
                        SheetClass.MULTI_TABLE,
                    ):
                        need_attention += 1  # ingested but needs attention
            except Exception as exc:  # noqa: BLE001
                tr.ingested = False
                tr.reason = f"ingest_error:{exc}"[:180]
                need_attention += 1
                fates = _fail_accepted(fates, tr.reason)
            _remember(log_rows, source, fates)
            results.append(tr)

    # Row fates are known before serving sync. Write them first so a sync
    # failure cannot skip the audit of what landed.
    _write_land_row_log(db_path, ingest_id, log_rows)

    # The sync outcome rides out on the receipt, not only in a log line. A
    # customer reads the receipt; nobody reads the warning (R-0011).
    sync_state = "not_attempted"
    sync_detail = ""
    if ingested_count:
        synced = maybe_sync_bronze_to_serving(db_path)
        if synced is None:
            # No serving warehouse named, sync disabled, or under pytest. Nothing
            # was copied and nothing was verified - which is a third answer, not
            # a quiet success.
            sync_state, sync_detail = "not_attempted", "no serving warehouse configured"
        elif synced.ok:
            sync_state = "not_needed" if synced.status == "same_file" else "ok"
            sync_detail = synced.status
        else:
            sync_state = "failed"
            sync_detail = (
                synced.error
                or f"{synced.status}: {synced.serving} was not updated"
            )[:300]
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
        serving_sync=sync_state,
        serving_sync_detail=sync_detail,
    )
