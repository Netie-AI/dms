"""Local query slice — Space-scoped stub + optional Cortex forward with space header."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from dms.auth.router import caller_from_headers
from settings import get_settings

router = APIRouter()


class QueryBody(BaseModel):
    question: str
    session_id: str = "demo"
    space_id: str | None = None


@router.post("/query-local")
async def query_local(
    body: QueryBody,
    caller: dict = Depends(caller_from_headers),
    x_space_id: str | None = Header(default=None, alias="X-Space-Id"),
) -> dict:
    """Space-aware entry: validates space exists then proxies to Cortex /dms/query with provenance note."""
    import httpx

    space_id = body.space_id or x_space_id
    space_meta = None
    if space_id:
        from adapters.postgres import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM spaces WHERE id = %s", (space_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="space not found")
                cur.execute(
                    "SELECT kind, ref, scope FROM space_sources WHERE space_id = %s",
                    (space_id,),
                )
                sources = [{"kind": k, "ref": r, "scope": s} for k, r, s in cur.fetchall()]
                space_meta = {"id": str(row[0]), "name": row[1], "sources": sources}

    settings = get_settings()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{settings.cortex_url.rstrip('/')}/dms/query",
            json={"question": body.question, "session_id": body.session_id},
            headers={"X-API-Key": "dms-demo-steward-key", "Content-Type": "application/json"},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    out = r.json()
    out["dms_gateway"] = True
    out["caller_role"] = caller["role"]
    if space_meta:
        out["space"] = space_meta
        out["query_source"] = out.get("query_source") or "space_scoped_proxy"
        # Honest note until extract: Cortex still sees full warehouse; enforcement lands with lake extract.
        out["assumptions"] = (
            (out.get("assumptions") or "")
            + f" | DMS Space '{space_meta['name']}' selected ({len(space_meta['sources'])} sources); "
            "hard path-manifest enforcement pending data-plane extract."
        ).strip(" |")
    return out
