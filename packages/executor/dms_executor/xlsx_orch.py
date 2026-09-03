"""EPIC-016 extract/store: byte-faithful xlsx under space_docs.

Swap scenario: local FS -> MinIO / S3 via ObjectStorePort. No duckdb.execute.
Excel is source-only: this copies posted bytes; it does not Workbook.save.
Caller-supplied paths must resolve under allowlisted_roots (warehouse parent
+ DMS_REVEAL_ROOTS). Bytes posted in the body skip the read-side check.
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
from dms_executor.reveal import resolve_allowlisted_file
from dms_executor.triage import parse_xlsx_grids


def space_docs_root(warehouse: Path | None = None) -> Path:
    db = warehouse or warehouse_path()
    return db.parent / "space_docs"


def _path_outside(path: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "rejected",
        "reason": "path_not_allowlisted",
        "error": f"path is outside allowlisted roots: {path}",
        "paste_owner": "pointer",
    }


def _load_sheets_from_path(path: str | Path) -> list[tuple[str, list[list[Any]]]] | dict[str, Any]:
    raw = str(path or "").strip()
    hit = resolve_allowlisted_file(raw)
    if not hit.get("ok"):
        if hit.get("error") == "path_not_allowlisted":
            return _path_outside(raw)
        return {
            "ok": False,
            "status": "rejected",
            "reason": "workbook_unreadable",
            "error": f"cannot read workbook_path: {raw}",
            "paste_owner": "pointer",
        }
    resolved: Path = hit["path"]
    try:
        sheets = parse_xlsx_grids(resolved.read_bytes())
    except Exception:  # noqa: BLE001
        return {
            "ok": False,
            "status": "rejected",
            "reason": "workbook_unreadable",
            "error": f"cannot read workbook_path: {raw}",
            "paste_owner": "pointer",
        }
    return sheets


def run_crosscheck(
    pack: dict[str, Any],
    *,
    workbook_path: str = "",
    pack_id: str | None = None,
) -> dict[str, Any]:
    path = str(workbook_path or "").strip()
    sheets: list[tuple[str, list[list[Any]]]] | None = None
    if path:
        loaded = _load_sheets_from_path(path)
        if isinstance(loaded, dict):
            return loaded
        sheets = loaded
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
        hit = resolve_allowlisted_file(src)
        if not hit.get("ok"):
            if hit.get("error") == "path_not_allowlisted":
                return _path_outside(src)
            return {
                "ok": False,
                "status": "awaiting_pointer_receipt",
                "reason": "result_path_missing",
                "error": f"result_path is not a file: {src}",
            }
        resolved: Path = hit["path"]
        raw = resolved.read_bytes()
        name = resolved.name
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
    src = str(path or (meta or {}).get("path") or "").strip()
    if not src:
        return {
            "ok": False,
            "reason": "awaiting_pointer_receipt",
            "error": (
                "no stored Copilot-path artifact. Unlock: Pointer posts the "
                "resulting xlsx to /v1/studio/xlsx-orch/extract"
            ),
        }
    loaded = _load_sheets_from_path(src)
    if isinstance(loaded, dict):
        if loaded.get("reason") == "path_not_allowlisted":
            return loaded
        return {"ok": False, "reason": "xlsx_read_error", "error": loaded.get("error")}
    prod = producer or (meta or {}).get("producer")
    return evaluate_frtr_golden(loaded, producer=prod)
