"""EPIC-016 extract/store: byte-faithful xlsx under space_docs.

Swap scenario: local FS -> MinIO / S3 via ObjectStorePort. No duckdb.execute.
Excel is source-only: this copies posted bytes; it does not Workbook.save.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dms_core.xlsx_orch import (
    crosscheck_pack,
    evaluate_frtr_golden,
    inspect_result_grids,
    load_artifact_meta,
    store_artifact,
)

from dms_executor.demo_warehouse import warehouse_path
from dms_executor.triage import parse_xlsx_grids


def space_docs_root(warehouse: Path | None = None) -> Path:
    db = warehouse or warehouse_path()
    return db.parent / "space_docs"


def _load_sheets_from_path(path: str | Path) -> list[tuple[str, list[list[Any]]]] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return parse_xlsx_grids(p.read_bytes())
    except Exception:  # noqa: BLE001
        return None


def run_crosscheck(
    pack: dict[str, Any],
    *,
    workbook_path: str = "",
    pack_id: str | None = None,
) -> dict[str, Any]:
    path = str(workbook_path or "").strip()
    sheets = _load_sheets_from_path(path) if path else None
    if path and sheets is None:
        return {
            "ok": False,
            "status": "rejected",
            "reason": "workbook_unreadable",
            "error": f"cannot read workbook_path: {path}",
            "paste_owner": "pointer",
        }
    return crosscheck_pack(
        pack,
        sheets=sheets,
        workbook_path=path,
        pack_id=pack_id,
    )


def run_extract(
    *,
    pack_id: str,
    space_id: str,
    producer: str,
    result_path: str = "",
    filename: str = "",
    data: bytes | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any]:
    raw = data
    name = filename or "result.xlsx"
    if raw is None:
        src = str(result_path or "").strip()
        if not src:
            return {
                "ok": False,
                "status": "awaiting_pointer_receipt",
                "reason": "awaiting_pointer_receipt",
                "error": (
                    "no result_path. Pointer must paste into Excel Copilot, then "
                    "POST the resulting xlsx. DMS does not invent a workbook."
                ),
            }
        p = Path(src)
        if not p.is_file():
            return {
                "ok": False,
                "status": "awaiting_pointer_receipt",
                "reason": "result_path_missing",
                "error": f"result_path is not a file: {p}",
            }
        raw = p.read_bytes()
        name = p.name
    try:
        sheets = parse_xlsx_grids(raw)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": "xlsx_read_error",
            "error": str(exc)[:200],
        }
    inspected = inspect_result_grids(sheets)
    stored = store_artifact(
        space_docs_root(warehouse),
        data=raw,
        filename=name,
        space_id=space_id or "company-default",
        pack_id=pack_id or "pack",
        producer=producer,
        sheetnames=list(inspected.get("sheetnames") or []),
    )
    if not stored.get("ok"):
        return stored
    stored["inspected"] = inspected
    stored["families"] = inspected.get("families")
    stored["missing_sheets"] = inspected.get("missing") or []
    return stored


def run_golden(
    *,
    pack_id: str = "",
    space_id: str = "",
    path: str = "",
    producer: str | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any]:
    meta = None
    if pack_id:
        meta = load_artifact_meta(
            space_docs_root(warehouse),
            space_id=space_id or "company-default",
            pack_id=pack_id,
        )
    src = path or (meta or {}).get("path") or ""
    if not src:
        return {
            "ok": False,
            "reason": "awaiting_pointer_receipt",
            "error": (
                "no stored Copilot-path artifact. Unlock: Pointer posts the "
                "resulting xlsx to /v1/studio/xlsx-orch/extract"
            ),
        }
    p = Path(str(src))
    if not p.is_file():
        return {
            "ok": False,
            "reason": "artifact_missing",
            "error": f"stored path is not a file: {p}",
        }
    sheets = _load_sheets_from_path(p)
    if sheets is None:
        return {"ok": False, "reason": "xlsx_read_error", "error": f"cannot read {p}"}
    prod = producer or (meta or {}).get("producer")
    return evaluate_frtr_golden(sheets, producer=prod)
