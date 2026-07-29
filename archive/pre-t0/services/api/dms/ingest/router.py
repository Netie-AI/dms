"""Ingest slice — proxies to Cortex; records ledger intent locally."""

from __future__ import annotations

import hashlib
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from adapters.postgres import get_conn
from dms.auth.router import caller_from_headers, ensure_seed
from settings import get_settings

router = APIRouter()


class UploadBody(BaseModel):
    filename: str
    content_b64: str
    space_id: str | None = None


@router.post("/ingest/file")
async def ingest_file(body: UploadBody, caller: dict = Depends(caller_from_headers)) -> dict:
    if caller["role"] not in ("steward", "admin"):
        raise HTTPException(status_code=403, detail="steward or admin required")
    ensure_seed()
    settings = get_settings()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{settings.cortex_url.rstrip('/')}/dms/ingest/file",
            json={"filename": body.filename, "content_b64": body.content_b64},
            headers={"X-API-Key": "dms-demo-steward-key", "Content-Type": "application/json"},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    result = r.json()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM orgs WHERE slug = 'default'")
                org = cur.fetchone()
                org_id = org[0] if org else None
                if org_id:
                    payload = {
                        "filename": body.filename,
                        "space_id": body.space_id,
                        "cortex": result,
                    }
                    body_s = json.dumps(payload, sort_keys=True)
                    entry_hash = hashlib.sha256(body_s.encode()).hexdigest()
                    cur.execute(
                        """
                        INSERT INTO ledger (org_id, actor, event_type, payload, entry_hash)
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        """,
                        (org_id, str(caller.get("email") or caller["user_id"]), "ingest.file", body_s, entry_hash),
                    )
                    if body.space_id and result.get("table"):
                        cur.execute(
                            """
                            INSERT INTO space_sources (space_id, kind, ref, scope, meta)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                body.space_id,
                                "table",
                                f"bronze.{result.get('table')}",
                                "personal",
                                json.dumps({"filename": body.filename}),
                            ),
                        )
            conn.commit()
    except Exception:
        pass
    result["dms_gateway"] = True
    return result
