"""Studio ingest — triage-first batch receipts; Excel readable, never outbound-written."""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import batch_ingest, bronze_list, list_document_chunks

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
    #: Whether chat can actually see what just landed (#75). Bronze landing and chat
    #: being able to read it are two different events; the receipt used to report only
    #: the first. Never None on a real receipt - "not attempted" is a state, not an
    #: absence, and an omitted field would read as "fine" (R-0011).
    serving_sync: dict[str, Any] | None = None


@router.post("/ingest")
async def ingest_file(
    cortex: CortexDep,
    file: UploadFile = File(...),
    space_id: str | None = Form(None),
) -> ReceiptOut:
    """Single-file ingest (compat). Prefer /ingest-batch for multi-file triage."""
    decision = compliance_gate(
        action="studio.ingest",
        metadata={"task_id": "studio.ingest", "filename": file.filename},
        client=cortex,
    )
    enforce(decision)

    raw = await file.read()
    name = file.filename or "upload.csv"
    receipt = batch_ingest([(name, raw)], space_id=space_id)
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
        serving_sync=receipt.get("serving_sync"),
    )


@router.post("/ingest-batch")
async def ingest_batch_files(
    cortex: CortexDep,
    files: list[UploadFile] = File(...),
    space_id: str | None = Form(None),
) -> ReceiptOut:
    decision = compliance_gate(
        action="studio.ingest",
        metadata={
            "task_id": "studio.ingest",
            "file_count": len(files),
        },
        client=cortex,
    )
    enforce(decision)

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        payloads.append((f.filename or "upload.csv", await f.read()))
    receipt = batch_ingest(payloads, space_id=space_id)
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
        serving_sync=receipt.get("serving_sync"),
    )


@router.get("/bronze")
def list_bronze(
    cortex: CortexDep,
    space_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    decision = compliance_gate(
        action="studio.list_bronze",
        metadata={"task_id": "studio.list_bronze", "space_id": space_id or "company-default"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return bronze_list(space_id=space_id)


@router.get("/chunks")
def list_chunks(
    cortex: CortexDep,
    settings: SettingsDep,
    space_id: str = Query(..., description="Space that owns the document chunks"),
) -> list[dict[str, Any]]:
    """Steward list of space-scoped document chunks (RAG-01)."""
    _ = settings
    decision = compliance_gate(
        action="studio.list_chunks",
        metadata={"task_id": "studio.list_chunks", "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return list_document_chunks(space_id=space_id)
