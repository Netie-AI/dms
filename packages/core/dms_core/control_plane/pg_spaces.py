"""Postgres Spaces repository (T3) — RLS via set_tenant_context."""

from __future__ import annotations

from uuid import UUID

import psycopg

from dms_core.control_plane.session import AppRole, set_tenant_context
from dms_core.control_plane.spaces import SpaceRecord


class PostgresSpaceStore:
    def __init__(
        self,
        conninfo: str,
        *,
        tenant_id: UUID | str,
        role: AppRole = "steward",
    ) -> None:
        self._conninfo = conninfo
        self._tenant_id = str(tenant_id)
        self._role = role

    def list_spaces(self) -> list[SpaceRecord]:
        with psycopg.connect(self._conninfo) as conn:
            set_tenant_context(conn, self._tenant_id, role=self._role)
            rows = conn.execute(
                """
                SELECT s.id::text, s.name,
                       (SELECT COUNT(*) FROM dms.data_sources d
                         WHERE d.space_id = s.id AND d.tenant_id = s.tenant_id),
                       (SELECT COUNT(*) FROM dms.space_members m
                         WHERE m.space_id = s.id AND m.tenant_id = s.tenant_id)
                  FROM dms.spaces s
                 WHERE s.tenant_id::text = %s AND s.state = 'active'
                 ORDER BY s.name
                """,
                (self._tenant_id,),
            ).fetchall()
            conn.commit()
        return [
            SpaceRecord(id=r[0], name=r[1], source_count=int(r[2]), member_count=int(r[3]))
            for r in rows
        ]

    def get(self, space_id: str) -> SpaceRecord | None:
        with psycopg.connect(self._conninfo) as conn:
            set_tenant_context(conn, self._tenant_id, role=self._role)
            row = conn.execute(
                """
                SELECT s.id::text, s.name,
                       (SELECT COUNT(*) FROM dms.data_sources d
                         WHERE d.space_id = s.id AND d.tenant_id = s.tenant_id),
                       (SELECT COUNT(*) FROM dms.space_members m
                         WHERE m.space_id = s.id AND m.tenant_id = s.tenant_id)
                  FROM dms.spaces s
                 WHERE s.tenant_id::text = %s AND s.id::text = %s
                """,
                (self._tenant_id, space_id),
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return SpaceRecord(
            id=row[0], name=row[1], source_count=int(row[2]), member_count=int(row[3])
        )
