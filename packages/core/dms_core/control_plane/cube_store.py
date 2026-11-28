"""Metric cube rows (FEAT-METRIC-CUBE-01).

Swap: in-memory, or Postgres over ``dms.metric_cubes`` when
``DMS_METRIC_CUBE=1`` and ``DATABASE_URL`` is set. Not a new port and not a
new config key. Flag off keeps the in-memory store and the ask path does
not read it. A proposal is never served. ``approve`` is the only path to
``approved``.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg

from dms_core.control_plane.session import set_tenant_context

_LOCK = threading.Lock()
_DEFAULT_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_STATUSES = frozenset({"proposed", "approved", "revoked"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def metric_cube_flag() -> bool:
    """True only when ``DMS_METRIC_CUBE`` is the digit 1. Unset is off."""
    return os.environ.get("DMS_METRIC_CUBE", "0").strip() == "1"


@dataclass
class CubeRow:
    id: str
    tenant_id: str
    space_id: str
    name: str
    body: dict[str, Any]
    status: str
    created_by: str
    approved_by: str
    ts: str


def _public(row: CubeRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "space_id": row.space_id,
        "name": row.name,
        "body": dict(row.body),
        "status": row.status,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "ts": row.ts,
        "served": row.status == "approved",
    }


class CubeStore:
    """Process-local cubes. Tests and flag-off use this."""

    def __init__(self) -> None:
        self._rows: dict[str, CubeRow] = {}

    def store_proposal(
        self,
        body: Mapping[str, Any],
        *,
        space_id: str,
        created_by: str,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        payload = dict(body)
        payload["status"] = "proposed"
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("cube_name_missing")
        row = CubeRow(
            id=str(uuid4()),
            tenant_id=tenant_id,
            space_id=str(space_id or ""),
            name=name,
            body=payload,
            status="proposed",
            created_by=created_by,
            approved_by="",
            ts=_now(),
        )
        with _LOCK:
            self._rows[row.id] = row
        out = _public(row)
        out["served"] = False
        out["status"] = "proposed"
        return out

    def approve(self, cube_id: str, *, actor: str) -> dict[str, Any] | None:
        with _LOCK:
            row = self._rows.get(str(cube_id or ""))
            if row is None or row.status == "revoked":
                return None
            for other in self._rows.values():
                if (
                    other.id != row.id
                    and other.space_id == row.space_id
                    and other.name == row.name
                    and other.status == "approved"
                ):
                    other.status = "revoked"
                    other.body = {**other.body, "status": "revoked"}
            body = dict(row.body)
            body["status"] = "approved"
            row.body = body
            row.status = "approved"
            row.approved_by = actor
            row.ts = _now()
            return _public(row)

    def approved(self, space_id: str) -> list[dict[str, Any]]:
        key = str(space_id or "")
        with _LOCK:
            rows = [
                _public(row)
                for row in self._rows.values()
                if row.space_id == key and row.status == "approved"
            ]
        return rows


class PostgresCubeStore:
    """``dms.metric_cubes``. Same methods as the memory store."""

    def __init__(self, url: str, *, tenant_id: str) -> None:
        self._url = url
        self._tenant = tenant_id

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._url, connect_timeout=3)

    def store_proposal(
        self,
        body: Mapping[str, Any],
        *,
        space_id: str,
        created_by: str,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        payload = dict(body)
        payload["status"] = "proposed"
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("cube_name_missing")
        row_id = str(uuid4())
        tenant = tenant_id or self._tenant
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            conn.execute(
                """
                INSERT INTO dms.metric_cubes (
                  id, tenant_id, space_id, name, body, status, created_by, approved_by
                ) VALUES (%s, %s, %s, %s, %s::jsonb, 'proposed', %s, '')
                """,
                (row_id, tenant, str(space_id or ""), name, json.dumps(payload), created_by),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "id": row_id,
            "space_id": str(space_id or ""),
            "name": name,
            "body": payload,
            "status": "proposed",
            "created_by": created_by,
            "approved_by": "",
            "ts": _now(),
            "served": False,
        }

    def approve(self, cube_id: str, *, actor: str) -> dict[str, Any] | None:
        tenant = self._tenant
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            current = conn.execute(
                """
                SELECT id::text, space_id, name, body
                  FROM dms.metric_cubes
                 WHERE id = %s AND status <> 'revoked'
                """,
                (str(cube_id),),
            ).fetchone()
            if current is None:
                conn.rollback()
                return None
            _id, space_id, name, body = current
            conn.execute(
                """
                UPDATE dms.metric_cubes
                   SET status = 'revoked'
                 WHERE space_id = %s AND name = %s AND status = 'approved' AND id <> %s
                """,
                (space_id, name, _id),
            )
            payload = body if isinstance(body, dict) else json.loads(body)
            payload["status"] = "approved"
            conn.execute(
                """
                UPDATE dms.metric_cubes
                   SET status = 'approved', approved_by = %s, body = %s::jsonb, ts = now()
                 WHERE id = %s
                """,
                (actor, json.dumps(payload), _id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "id": str(cube_id),
            "status": "approved",
            "served": True,
            "body": payload,
            "name": name,
            "space_id": space_id,
        }

    def approved(self, space_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            set_tenant_context(conn, self._tenant, role="viewer")
            fetched = conn.execute(
                """
                SELECT id::text, space_id, name, body, status, created_by, approved_by, ts
                  FROM dms.metric_cubes
                 WHERE space_id = %s AND status = 'approved'
                """,
                (str(space_id or ""),),
            ).fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        out: list[dict[str, Any]] = []
        for row in fetched:
            body = row[3] if isinstance(row[3], dict) else json.loads(row[3])
            if str(body.get("status") or "") != "approved":
                body = {**body, "status": "approved"}
            out.append(
                {
                    "id": row[0],
                    "space_id": row[1],
                    "name": row[2],
                    "body": body,
                    "status": row[4] if row[4] in _STATUSES else "approved",
                    "created_by": row[5] or "",
                    "approved_by": row[6] or "",
                    "ts": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]),
                    "served": True,
                }
            )
        return out


_MEMORY = CubeStore()
_PG: PostgresCubeStore | None = None
_PG_KEY: tuple[str, str] | None = None


def cube_store() -> CubeStore | PostgresCubeStore:
    """Memory, unless the cube flag is on and ``DATABASE_URL`` is set."""
    global _PG, _PG_KEY
    url = os.environ.get("DATABASE_URL", "").strip()
    if not metric_cube_flag() or not url:
        return _MEMORY
    tenant = os.environ.get("DMS_TENANT_ID", "").strip() or _DEFAULT_TENANT
    key = (url, tenant)
    if _PG is None or _PG_KEY != key:
        _PG = PostgresCubeStore(url, tenant_id=tenant)
        _PG_KEY = key
    return _PG


def reset_cube_store() -> None:
    global _MEMORY, _PG, _PG_KEY
    with _LOCK:
        _MEMORY = CubeStore()
        _PG = None
        _PG_KEY = None
