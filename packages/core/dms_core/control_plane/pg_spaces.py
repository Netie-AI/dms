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

    def create(self, name: str) -> SpaceRecord:
        """Persist a Space and make its creator the first member.

        The memory store has had this since the beginning; this one did not, and
        the route reached it through ``getattr(store, "create", None)`` — so
        creating a Space worked right up until Postgres was actually wired, then
        started answering 501. Both stores now satisfy the same port, and the
        port declares ``create``, so a store missing it is a type error rather
        than a runtime capability gap discovered in production.

        The member row is what makes ``member_count`` 1 on the way out, matching
        the memory store's contract, and it is what a Space ACL will read once
        the ask path stops minting a demo manifest.
        """
        clean = name.strip()
        if not clean:
            raise ValueError("space_name_required")
        with psycopg.connect(self._conninfo) as conn:
            set_tenant_context(conn, self._tenant_id, role="steward")
            try:
                row = conn.execute(
                    """
                    INSERT INTO dms.spaces (tenant_id, name, created_by)
                    VALUES (%s, %s, (SELECT user_id FROM dms.memberships
                                      WHERE tenant_id::text = %s LIMIT 1))
                    RETURNING id::text, name
                    """,
                    (self._tenant_id, clean, self._tenant_id),
                ).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                # UNIQUE (tenant_id, name) — same 409 the memory store produces,
                # so the route's existing conflict branch keeps working.
                conn.rollback()
                raise ValueError("space_name_taken") from exc
            assert row is not None
            space_id = row[0]
            conn.execute(
                """
                INSERT INTO dms.space_members (space_id, tenant_id, user_id)
                SELECT %s, %s, user_id FROM dms.memberships
                 WHERE tenant_id::text = %s LIMIT 1
                """,
                (space_id, self._tenant_id, self._tenant_id),
            )
            members = conn.execute(
                """
                SELECT COUNT(*) FROM dms.space_members
                 WHERE space_id::text = %s AND tenant_id::text = %s
                """,
                (space_id, self._tenant_id),
            ).fetchone()
            conn.commit()
        return SpaceRecord(
            id=space_id,
            name=row[1],
            source_count=0,
            member_count=int(members[0]) if members else 0,
        )

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
