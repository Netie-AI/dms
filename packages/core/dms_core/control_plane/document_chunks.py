"""Space-scoped document chunk index — Postgres ``dms.document_chunks`` (RAG-01)."""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID, uuid4

import psycopg

from dms_core.control_plane.session import AppRole, set_tenant_context

# ponytail: char-trigram bag — upgrade to pgvector HNSW + model_provider embed
_DENSE_DIMS = 64


def _dense_vector(text: str, *, dims: int = _DENSE_DIMS) -> list[float]:
    vec = [0.0] * dims
    body = (text or "").lower()
    for i in range(max(0, len(body) - 2)):
        vec[hash(body[i : i + 3]) % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm:
        vec = [x / norm for x in vec]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def register_document_source(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    ref: str,
    blob_key: str | None = None,
) -> UUID:
    """Insert a ``data_sources`` row for an unstructured upload; return source_id."""
    meta: dict[str, Any] = {}
    if blob_key:
        meta["blob_key"] = blob_key
    row = conn.execute(
        """
        INSERT INTO dms.data_sources (tenant_id, space_id, kind, ref, scope, meta)
        VALUES (%s, %s, 'document', %s, 'team', %s::jsonb)
        RETURNING id
        """,
        (str(tenant_id), str(space_id), ref, json.dumps(meta)),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def write_chunks(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    source_id: UUID | str,
    chunks: list[str],
    blob_key: str | None = None,
    embed_meta: dict[str, Any] | None = None,
) -> int:
    """Persist chunk rows bound to ``(tenant_id, space_id, source_id)``. Returns count."""
    if not space_id:
        raise ValueError("space_id_required")
    base_meta = embed_meta if embed_meta is not None else {"status": "pending"}
    n = 0
    for i, text in enumerate(chunks):
        body = text.strip()
        if not body:
            continue
        meta = dict(base_meta)
        if "dense" not in meta:
            meta["status"] = "hybrid"
            meta["dense"] = _dense_vector(body)
        meta_json = json.dumps(meta)
        conn.execute(
            """
            INSERT INTO dms.document_chunks
              (tenant_id, space_id, source_id, chunk_index, content, blob_key, embed_meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                str(tenant_id),
                str(space_id),
                str(source_id),
                i,
                body,
                blob_key,
                meta_json,
            ),
        )
        n += 1
    return n


def index_document(
    conninfo: str,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    ref: str,
    chunks: list[str],
    blob_key: str | None = None,
    role: AppRole = "steward",
) -> tuple[str, int]:
    """Create source + write chunks. Returns ``(source_id, chunk_count)``."""
    if not space_id:
        raise ValueError("space_id_required")
    with psycopg.connect(conninfo) as conn:
        set_tenant_context(conn, tenant_id, role=role)
        # Confirm space belongs to tenant (no cross-tenant write).
        owned = conn.execute(
            """
            SELECT 1 FROM dms.spaces
             WHERE id::text = %s AND tenant_id::text = %s
            """,
            (str(space_id), str(tenant_id)),
        ).fetchone()
        if owned is None:
            raise ValueError("space_not_in_tenant")
        source_id = register_document_source(
            conn,
            tenant_id=tenant_id,
            space_id=space_id,
            ref=ref,
            blob_key=blob_key,
        )
        count = write_chunks(
            conn,
            tenant_id=tenant_id,
            space_id=space_id,
            source_id=source_id,
            chunks=chunks,
            blob_key=blob_key,
        )
        conn.commit()
    return str(source_id), count


def search_chunks(
    conninfo: str,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    q: str,
    top_k: int = 8,
    source_ids: list[str] | None = None,
    role: AppRole = "viewer",
) -> list[dict[str, Any]]:
    """Lexical search over chunk content — scope filter is in SQL (``WHERE space_id``)."""
    if not space_id:
        return []
    query = (q or "").strip()
    if not query:
        return []
    limit = max(1, min(int(top_k), 100))
    query_vec = _dense_vector(query)
    with psycopg.connect(conninfo) as conn:
        set_tenant_context(conn, tenant_id, role=role)
        params: list[Any] = [query.lower(), str(tenant_id), str(space_id)]
        source_clause = ""
        if source_ids:
            source_clause = "AND source_id::text = ANY(%s)"
            params.append([str(s) for s in source_ids])
        rows = conn.execute(
            f"""
            SELECT id::text, space_id::text, source_id::text, chunk_index,
                   content, blob_key, embed_meta, created_at::text, lexical_score
              FROM (
                SELECT id, space_id, source_id, chunk_index, content, blob_key,
                       embed_meta, created_at,
                       (
                         SELECT COUNT(*)::float
                           FROM unnest(
                             regexp_split_to_array(lower(%s), '\\s+')
                           ) AS w(token)
                          WHERE length(token) > 1
                            AND lower(content) LIKE '%%' || token || '%%'
                       ) AS lexical_score
                  FROM dms.document_chunks
                 WHERE tenant_id::text = %s
                   AND space_id::text = %s
                   {source_clause}
              ) ranked
             WHERE lexical_score > 0
             ORDER BY lexical_score DESC, chunk_index
            """,
            tuple(params),
        ).fetchall()
        conn.commit()
    hits: list[dict[str, Any]] = []
    for r in rows:
        meta = r[6] if isinstance(r[6], dict) else {}
        lexical = float(r[8])
        dense_vec = meta.get("dense") if isinstance(meta.get("dense"), list) else None
        dense = _cosine(query_vec, dense_vec) if dense_vec else 0.0
        score = lexical * 0.6 + dense * 0.4 if dense_vec else lexical
        hits.append(
            {
                "id": r[0],
                "space_id": r[1],
                "source_id": r[2],
                "chunk_index": int(r[3]),
                "content": r[4],
                "blob_key": r[5],
                "embed_meta": meta,
                "created_at": r[7],
                "score": score,
                "lexical_score": lexical,
                "dense_score": dense,
            }
        )
    hits.sort(key=lambda h: (-h["score"], h["chunk_index"]))
    return hits[:limit]


def list_chunks(
    conninfo: str,
    *,
    tenant_id: UUID | str,
    space_id: UUID | str,
    role: AppRole = "viewer",
) -> list[dict[str, Any]]:
    """Steward/viewer list of chunks for one Space — never crosses space_id."""
    with psycopg.connect(conninfo) as conn:
        set_tenant_context(conn, tenant_id, role=role)
        rows = conn.execute(
            """
            SELECT id::text, space_id::text, source_id::text, chunk_index,
                   content, blob_key, embed_meta, created_at::text
              FROM dms.document_chunks
             WHERE tenant_id::text = %s AND space_id::text = %s
             ORDER BY source_id, chunk_index
            """,
            (str(tenant_id), str(space_id)),
        ).fetchall()
        conn.commit()
    return [
        {
            "id": r[0],
            "space_id": r[1],
            "source_id": r[2],
            "chunk_index": int(r[3]),
            "content": r[4],
            "blob_key": r[5],
            "embed_meta": r[6] if isinstance(r[6], dict) else {},
            "created_at": r[7],
        }
        for r in rows
    ]


def new_source_id() -> str:
    """Helper for callers that mint an id before insert (tests)."""
    return str(uuid4())
