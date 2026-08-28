"""XLSX-ORCH-10 HTTP -- consume AirGPT pack, hand off to Pointer. Do not paste."""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import xlsx_orch_crosscheck, xlsx_orch_pointer_receipt

router = APIRouter(prefix="/v1/studio/xlsx-orch", tags=["xlsx-orch"])


class CrosscheckBody(BaseModel):
    pack: dict[str, Any]
    workbook_path: str | None = Field(default=None, max_length=2048)


class PointerReceiptBody(BaseModel):
    pack_id: str = Field(min_length=1, max_length=128)
    result_path: str = Field(min_length=1, max_length=2048)


@router.post("/crosscheck")
def crosscheck(
    body: CrosscheckBody,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Governed cross-check of an AirGPT pack. Pointer paste is not executed here."""
    _ = settings
    decision = compliance_gate(
        action="studio.xlsx_orch_crosscheck",
        metadata={"task_id": "studio.xlsx_orch_crosscheck"},
        client=cortex,
    )
    enforce(decision)
    try:
        return xlsx_orch_crosscheck(body.pack, workbook_path=body.workbook_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pointer-receipt")
def pointer_receipt(
    body: PointerReceiptBody,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Pointer posts the resulting workbook path. DMS does not paste or extract."""
    _ = settings
    decision = compliance_gate(
        action="studio.xlsx_orch_pointer_receipt",
        metadata={
            "task_id": "studio.xlsx_orch_pointer_receipt",
            "pack_id": body.pack_id,
        },
        client=cortex,
    )
    enforce(decision)
    try:
        return xlsx_orch_pointer_receipt(body.pack_id, body.result_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
