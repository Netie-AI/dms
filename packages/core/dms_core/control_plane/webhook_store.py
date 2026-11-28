"""Webhook subscriptions. The row stores an OpenVault reference, never the secret.

Swap: in-process list when Postgres is down, ``dms.webhook_subscriptions`` when
``DATABASE_URL`` is bound. Not a new port. Catalog already covers control-plane
rows. No hash chain here (the one ledger stays in Cortex).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg

from dms_core.control_plane.session import set_tenant_context


@dataclass(frozen=True)
class WebhookSubscription:
    id: str
    space_id: str
    url: str
    secret_ref: str
    events: tuple[str, ...]
    include_values: bool

    def public(self) -> dict[str, Any]:
        """JSON shape. ``secret_ref`` is a reference. There is no secret field."""
        return {
            "id": self.id,
            "space_id": self.space_id,
            "url": self.url,
            "secret_ref": self.secret_ref,
            "events": list(self.events),
            "include_values": self.include_values,
        }


def _proof(
    *,
    space_id: str,
    event: str,
    outcome: str,
    route: str | None,
    audit_id: str | None,
    run_id: str | None,
) -> dict[str, Any]:
    """Ids and the outcome only. Row values are not a field on this record."""
    return {
        "space_id": space_id,
        "event": event,
        "outcome": outcome,
        "route": route,
        "audit_id": audit_id,
        "run_id": run_id,
    }


class WebhookStore(Protocol):
    def add(self, sub: WebhookSubscription) -> WebhookSubscription: ...

    def list_for_space(self, space_id: str) -> list[WebhookSubscription]: ...

    def note_proof(
        self,
        *,
        space_id: str,
        event: str,
        outcome: str,
        route: str | None,
        audit_id: str | None,
        run_id: str | None,
    ) -> dict[str, Any]: ...

    def latest_proof(self, space_id: str) -> dict[str, Any] | None: ...


class InMemoryWebhookStore:
    """Process-local book. Tests and a DMS with no DATABASE_URL use this."""

    backend = "memory"

    def __init__(self) -> None:
        self._subs: dict[str, WebhookSubscription] = {}
        self._proofs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(self, sub: WebhookSubscription) -> WebhookSubscription:
        with self._lock:
            self._subs[sub.id] = sub
        return sub

    def list_for_space(self, space_id: str) -> list[WebhookSubscription]:
        with self._lock:
            rows = [row for row in self._subs.values() if row.space_id == space_id]
        return rows

    def note_proof(
        self,
        *,
        space_id: str,
        event: str,
        outcome: str,
        route: str | None,
        audit_id: str | None,
        run_id: str | None,
    ) -> dict[str, Any]:
        body = _proof(
            space_id=space_id,
            event=event,
            outcome=outcome,
            route=route,
            audit_id=audit_id,
            run_id=run_id,
        )
        with self._lock:
            self._proofs[space_id] = body
        return body

    def latest_proof(self, space_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._proofs.get(space_id)
        return dict(found) if found is not None else None


class PostgresWebhookStore:
    """``dms.webhook_*`` under RLS. Writes as steward. Reads as steward too,
    so one deployment identity can register and then list.
    """

    backend = "postgres"

    def __init__(self, conninfo: str, *, tenant_id: UUID | str) -> None:
        self._conninfo = conninfo
        self._tenant_id = str(tenant_id)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._conninfo, connect_timeout=5)

    def add(self, sub: WebhookSubscription) -> WebhookSubscription:
        with self._connect() as conn:
            set_tenant_context(conn, self._tenant_id, role="steward")
            conn.execute(
                """
                INSERT INTO dms.webhook_subscriptions
                  (id, tenant_id, space_id, url, secret_ref, events, include_values)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sub.id,
                    self._tenant_id,
                    sub.space_id,
                    sub.url,
                    sub.secret_ref,
                    list(sub.events),
                    sub.include_values,
                ),
            )
        return sub

    def list_for_space(self, space_id: str) -> list[WebhookSubscription]:
        with self._connect() as conn:
            set_tenant_context(conn, self._tenant_id, role="steward")
            rows = conn.execute(
                """
                SELECT id::text, space_id, url, secret_ref, events, include_values
                  FROM dms.webhook_subscriptions
                 WHERE space_id = %s
                 ORDER BY created_at
                """,
                (space_id,),
            ).fetchall()
        out: list[WebhookSubscription] = []
        for row in rows:
            events = row[4] if isinstance(row[4], (list, tuple)) else ()
            out.append(
                WebhookSubscription(
                    id=str(row[0]),
                    space_id=str(row[1]),
                    url=str(row[2]),
                    secret_ref=str(row[3]),
                    events=tuple(str(item) for item in events),
                    include_values=bool(row[5]),
                )
            )
        return out

    def note_proof(
        self,
        *,
        space_id: str,
        event: str,
        outcome: str,
        route: str | None,
        audit_id: str | None,
        run_id: str | None,
    ) -> dict[str, Any]:
        body = _proof(
            space_id=space_id,
            event=event,
            outcome=outcome,
            route=route,
            audit_id=audit_id,
            run_id=run_id,
        )
        with self._connect() as conn:
            set_tenant_context(conn, self._tenant_id, role="steward")
            conn.execute(
                """
                INSERT INTO dms.webhook_events
                  (id, tenant_id, space_id, event, outcome, route, audit_id, run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid4()),
                    self._tenant_id,
                    space_id,
                    event,
                    outcome,
                    route or "",
                    audit_id,
                    run_id,
                ),
            )
        return body

    def latest_proof(self, space_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            set_tenant_context(conn, self._tenant_id, role="steward")
            row = conn.execute(
                """
                SELECT event, outcome, route, audit_id, run_id
                  FROM dms.webhook_events
                 WHERE space_id = %s
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (space_id,),
            ).fetchone()
        if row is None:
            return None
        return _proof(
            space_id=space_id,
            event=str(row[0]),
            outcome=str(row[1]),
            route=str(row[2]) or None,
            audit_id=None if row[3] is None else str(row[3]),
            run_id=None if row[4] is None else str(row[4]),
        )
