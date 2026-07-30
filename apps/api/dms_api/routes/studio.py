"""Studio ingest — triage-first batch receipts; Excel readable, never outbound-written."""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep
from dms_api.wiring import batch_ingest, bronze_list

router = APIRouter(prefix="/v1/studio", tags=["studio"])


class ReceiptOut(BaseModel):
    files_seen: int
    ingested: int
    quarantined: int = 0
    reasons: list[dict[str, str]] = Field(default_factory=list)
    ingest_id: str
    source_ref_id: str | None = None
    table: str | None = None
    need_attention: int | None = None
    per_class: dict[str, int] | None = None
    files: list[dict[str, Any]] | None = None
    summary: str | None = None


@router.post("/ingest")
async def ingest_file(
    cortex: CortexDep,
    file: UploadFile = File(...),
) -> ReceiptOut:
    """Single-file ingest (compat). Prefer /ingest-batch for multi-file triage."""
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
    receipt = batch_ingest([(name, raw)])
    attention = receipt.get("need_attention", 0)
    return ReceiptOut(
        files_seen=receipt["files_seen"],
        ingested=receipt["ingested"],
        quarantined=attention,
        reasons=[
            {"file": f["file"], "reason": f["reason"], "fix": f.get("fix", "")}
            for f in receipt.get("files", [])
            if f.get("classification") != "TABULAR_CLEAN"
        ],
        ingest_id=receipt["ingest_id"],
        source_ref_id=None,
        table=next(
            (f.get("table") for f in receipt.get("files", []) if f.get("table")),
            None,
        ),
        need_attention=attention,
        per_class=receipt.get("per_class"),
        files=receipt.get("files"),
        summary=receipt.get("summary"),
    )


@router.post("/ingest-batch")
async def ingest_batch_files(
    cortex: CortexDep,
    files: list[UploadFile] = File(...),
) -> ReceiptOut:
    decision = compliance_gate(
        action="studio.ingest",
        metadata={
            "task_id": "studio.ingest",
            "file_count": len(files),
        },
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        payloads.append((f.filename or "upload.csv", await f.read()))
    receipt = batch_ingest(payloads)
    attention = receipt.get("need_attention", 0)
    return ReceiptOut(
        files_seen=receipt["files_seen"],
        ingested=receipt["ingested"],
        quarantined=attention,
        reasons=[
            {"file": x["file"], "reason": x["reason"], "fix": x.get("fix", "")}
            for x in receipt.get("files", [])
            if x.get("classification") != "TABULAR_CLEAN"
        ],
        ingest_id=receipt["ingest_id"],
        need_attention=attention,
        per_class=receipt.get("per_class"),
        files=receipt.get("files"),
        summary=receipt.get("summary"),
    )


@router.get("/bronze")
def list_bronze(cortex: CortexDep) -> list[dict[str, Any]]:
    _ = cortex
    return bronze_list()
