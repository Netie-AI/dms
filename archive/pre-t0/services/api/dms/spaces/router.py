from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.postgres import get_conn, new_id, use_sqlite
from dms.auth.router import caller_from_headers, ensure_seed

router = APIRouter()


class SourceIn(BaseModel):
    kind: str
    ref: str
    scope: str = Field(pattern="^(personal|team|company)$")
    meta: dict[str, Any] = Field(default_factory=dict)


class SpaceCreate(BaseModel):
    name: str
    sources: list[SourceIn] = Field(default_factory=list)


def _org_id(cur, caller: dict):
    if caller["org_id"] == "default":
        cur.execute("SELECT id FROM orgs WHERE slug = 'default'")
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="default org missing")
        return row[0]
    return caller["org_id"]


def _ledger(conn, org_id: str, actor: str, event_type: str, payload: dict) -> None:
    body = json.dumps(payload, sort_keys=True)
    entry_hash = hashlib.sha256(f"{org_id}:{actor}:{event_type}:{body}".encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ledger (org_id, actor, event_type, payload, entry_hash)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (org_id, actor, event_type, body, entry_hash),
        )


@router.get("")
def list_spaces(caller: dict = Depends(caller_from_headers)) -> dict:
    ensure_seed()
    with get_conn() as conn:
        with conn.cursor() as cur:
            org_id = _org_id(cur, caller)
            cur.execute(
                "SELECT id, name, state FROM spaces WHERE org_id = %s AND state = 'active' ORDER BY created_at DESC",
                (org_id,),
            )
            rows = cur.fetchall()
        items = []
        for sid, name, state in rows:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT kind, ref, scope, meta FROM space_sources WHERE space_id = %s",
                    (sid,),
                )
                sources = []
                for k, r, s, m in cur.fetchall():
                    if isinstance(m, str):
                        try:
                            m = json.loads(m)
                        except Exception:
                            m = {}
                    sources.append({"kind": k, "ref": r, "scope": s, "meta": m or {}})
            items.append({"id": str(sid), "name": name, "state": state, "sources": sources})
    return {"spaces": items}


@router.post("")
def create_space(body: SpaceCreate, caller: dict = Depends(caller_from_headers)) -> dict:
    if caller["role"] not in ("steward", "admin"):
        raise HTTPException(status_code=403, detail="steward or admin required")
    ensure_seed()
    with get_conn() as conn:
        with conn.cursor() as cur:
            org_id = _org_id(cur, caller)
            if use_sqlite():
                sid = new_id()
                cur.execute(
                    "INSERT INTO spaces (id, org_id, name) VALUES (%s, %s, %s)",
                    (sid, org_id, body.name),
                )
                name, state = body.name, "active"
            else:
                cur.execute(
                    "INSERT INTO spaces (org_id, name) VALUES (%s, %s) RETURNING id, name, state",
                    (org_id, body.name),
                )
                sid, name, state = cur.fetchone()
            for src in body.sources:
                meta = json.dumps(src.meta)
                if use_sqlite():
                    cur.execute(
                        """
                        INSERT INTO space_sources (id, space_id, kind, ref, scope, meta)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (new_id(), sid, src.kind, src.ref, src.scope, meta),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO space_sources (space_id, kind, ref, scope, meta)
                        VALUES (%s, %s, %s, %s, %s::jsonb)
                        """,
                        (sid, src.kind, src.ref, src.scope, meta),
                    )
            _ledger(
                conn,
                str(org_id),
                str(caller.get("email") or caller["user_id"]),
                "space.created",
                {"space_id": str(sid), "name": name, "source_count": len(body.sources)},
            )
        conn.commit()
    return {
        "id": str(sid),
        "name": name,
        "state": state,
        "sources": [s.model_dump() for s in body.sources],
    }


@router.get("/{space_id}")
def get_space(space_id: str, caller: dict = Depends(caller_from_headers)) -> dict:
    ensure_seed()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, state FROM spaces WHERE id = %s", (space_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="space not found")
            cur.execute(
                "SELECT kind, ref, scope, meta FROM space_sources WHERE space_id = %s",
                (space_id,),
            )
            sources = [
                {"kind": k, "ref": r, "scope": s, "meta": m or {}}
                for k, r, s, m in cur.fetchall()
            ]
    return {"id": str(row[0]), "name": row[1], "state": row[2], "sources": sources}
