"""Studio ingest — triage-first batch receipts; Excel readable, never outbound-written."""

from __future__ import annotations

from typing import Any, Literal

from cortex_client import compliance_gate
from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.wiring import (
    batch_ingest,
    bronze_list,
    list_document_chunks,
    sql_source_describe,
    sql_source_ingest,
    xlsx_orch_crosscheck,
    xlsx_orch_extract,
    xlsx_orch_golden,
)

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
    #: "ok" | "not_needed" | "not_attempted" | "failed" - see TriageReceipt.
    #: Without this on the response model the field exists on the receipt and is
    #: dropped on the way out, which is the same silence in a new place.
    serving_sync: str | None = None
    serving_sync_detail: str | None = None
    chat_can_see_it: bool | None = None


@router.post("/ingest")
async def ingest_file(
    cortex: CortexDep,
    settings: SettingsDep,
    file: UploadFile = File(...),
    space_id: str | None = Form(None),
) -> ReceiptOut:
    """Single-file ingest (compat). Prefer /ingest-batch for multi-file triage."""
    decision = compliance_gate(
        action="studio.ingest",
        actor=settings.dms_actor_user_id,
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
        serving_sync_detail=receipt.get("serving_sync_detail"),
        chat_can_see_it=receipt.get("chat_can_see_it"),
    )


@router.post("/ingest-batch")
async def ingest_batch_files(
    cortex: CortexDep,
    settings: SettingsDep,
    files: list[UploadFile] = File(...),
    space_id: str | None = Form(None),
) -> ReceiptOut:
    decision = compliance_gate(
        action="studio.ingest",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.ingest", "file_count": len(files)},
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
        serving_sync_detail=receipt.get("serving_sync_detail"),
        chat_can_see_it=receipt.get("chat_can_see_it"),
    )


@router.get("/bronze")
def list_bronze(
    cortex: CortexDep,
    settings: SettingsDep,
    space_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    decision = compliance_gate(
        action="studio.list_bronze",
        actor=settings.dms_actor_user_id,
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
    decision = compliance_gate(
        action="studio.list_chunks",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.list_chunks", "space_id": space_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return list_document_chunks(space_id=space_id)


class XlsxOrchCrosscheckIn(BaseModel):
    pack: dict[str, Any]
    workbook_path: str = ""
    pack_id: str | None = None


class XlsxOrchExtractIn(BaseModel):
    pack_id: str
    space_id: str | None = None
    producer: str = "pointer_copilot"
    result_path: str = ""


class XlsxOrchGoldenIn(BaseModel):
    pack_id: str = ""
    space_id: str | None = None
    path: str = ""
    producer: str | None = None


@router.post("/xlsx-orch/crosscheck")
def xlsx_orch_crosscheck_route(
    body: XlsxOrchCrosscheckIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """AirGPT D04 POSTs a candidate pack here. DMS does not paste into Excel."""
    decision = compliance_gate(
        action="studio.xlsx_orch.crosscheck",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.xlsx_orch.crosscheck"},
        client=cortex,
    )
    enforce(decision)
    return xlsx_orch_crosscheck(
        body.pack,
        workbook_path=body.workbook_path,
        pack_id=body.pack_id,
    )


@router.post("/xlsx-orch/extract")
def xlsx_orch_extract_route(
    body: XlsxOrchExtractIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Store a Pointer-posted result xlsx. DMS does not generate the workbook."""
    decision = compliance_gate(
        action="studio.xlsx_orch.extract",
        actor=settings.dms_actor_user_id,
        metadata={
            "task_id": "studio.xlsx_orch.extract",
            "pack_id": body.pack_id,
            "space_id": body.space_id or "company-default",
        },
        client=cortex,
    )
    enforce(decision)
    return xlsx_orch_extract(
        pack_id=body.pack_id,
        space_id=body.space_id or "company-default",
        producer=body.producer,
        result_path=body.result_path,
    )


@router.post("/xlsx-orch/golden")
def xlsx_orch_golden_route(
    body: XlsxOrchGoldenIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """FRTR golden on a stored Copilot-path artifact. Refuses MCP/openpyxl producer."""
    decision = compliance_gate(
        action="studio.xlsx_orch.golden",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.xlsx_orch.golden", "pack_id": body.pack_id},
        client=cortex,
    )
    enforce(decision)
    return xlsx_orch_golden(
        pack_id=body.pack_id,
        space_id=body.space_id or "company-default",
        path=body.path,
        producer=body.producer,
    )


class SqlSourceIn(BaseModel):
    kind: Literal["sqlserver", "mysql"]
    host: str
    database: str
    user: str
    password: str = Field(default="", max_length=256)
    port: int | None = None
    tables: list[str] | None = None
    max_rows: int | None = None
    space_id: str | None = None
    encrypt: bool = True
    trust_server_certificate: bool = False


@router.post("/sources/sql")
def sql_source_ingest_route(
    body: SqlSourceIn,
    cortex: CortexDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Extract a SQL Server or MySQL source into bronze. Credentials are not stored."""
    label = sql_source_describe(
        kind=body.kind, host=body.host, database=body.database, port=body.port
    )
    decision = compliance_gate(
        action="studio.sql_source",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "studio.sql_source", "source": label, "kind": body.kind},
        client=cortex,
    )
    enforce(decision)
    return sql_source_ingest(
        kind=body.kind,
        host=body.host,
        database=body.database,
        user=body.user,
        password=body.password,
        port=body.port,
        tables=body.tables,
        max_rows=body.max_rows,
        space_id=body.space_id,
        encrypt=body.encrypt,
        trust_server_certificate=body.trust_server_certificate,
    )
