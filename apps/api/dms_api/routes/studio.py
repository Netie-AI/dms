"""Studio ingest — CSV bronze with provenance; Excel quarantined (source-only)."""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from dms_api.deps import CortexDep
from dms_api.wiring import bronze_ingest, bronze_list

router = APIRouter(prefix="/v1/studio", tags=["studio"])


class ReceiptOut(BaseModel):
    files_seen: int
    ingested: int
    quarantined: int
    reasons: list[dict[str, str]]
    ingest_id: str
    source_ref_id: str
    table: str | None = None


@router.post("/ingest")
async def ingest_file(
    cortex: CortexDep,
    file: UploadFile = File(...),
) -> ReceiptOut:
    decision = compliance_gate(
        action="studio.ingest",
        metadata={"task_id": "studio.ingest", "filename": file.filename},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)

    raw = await file.read()
    name = file.filename or "upload.csv"
    receipt = bronze_ingest(filename=name, data=raw)
    return ReceiptOut(
        files_seen=receipt.files_seen,
        ingested=receipt.ingested,
        quarantined=receipt.quarantined,
        reasons=receipt.reasons,
        ingest_id=receipt.ingest_id,
        source_ref_id=receipt.source_ref_id,
        table=receipt.table,
    )


@router.get("/bronze")
def list_bronze(cortex: CortexDep) -> list[dict[str, Any]]:
    _ = cortex
    return bronze_list()
