"""Per-company view of the catalog and the ask audit.

Installed only when ``DMS_SSO`` is on. Flag off leaves the stores app.py
built, so a deployment tenant read is the same object the process already
used. Another company's rows stay in that company's partition.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dms_core.control_plane.ask_audit import AskAuditRecord
from dms_core.control_plane.session import AppRole
from dms_core.control_plane.spaces import SpaceRecord, SpaceStorePort

from dms_api.auth_sso import Principal, bound_tenant_id, current_principal
from dms_api.store.memory import DemoSpaceStore

_seen: list[Any] = []


def _read_role(principal: Principal | None) -> AppRole:
    if principal is not None and principal.role == "admin":
        return "admin"
    if principal is not None and principal.role == "steward":
        return "steward"
    # analyst has no Postgres role of its own. Reads use dms_viewer.
    return "viewer"


class TenantSpaces:
    """One install, several companies. The deployment tenant keeps ``inner``."""

    def __init__(self, deployment_tenant: str, inner: SpaceStorePort) -> None:
        self._deployment_tenant = deployment_tenant
        self._deployment = inner
        self._by_tenant: dict[str, SpaceStorePort] = {}

    def _active(self) -> SpaceStorePort:
        tenant = bound_tenant_id(self._deployment_tenant)
        if tenant == self._deployment_tenant:
            return self._deployment
        found = self._by_tenant.get(tenant)
        if found is not None:
            return found
        conninfo = getattr(self._deployment, "_conninfo", None)
        if isinstance(conninfo, str) and conninfo:
            from dms_core.control_plane.pg_spaces import PostgresSpaceStore

            made: SpaceStorePort = PostgresSpaceStore(
                conninfo,
                tenant_id=tenant,
                role=_read_role(current_principal()),
            )
        else:
            made = DemoSpaceStore()
        self._by_tenant[tenant] = made
        return made

    def list_spaces(self) -> list[SpaceRecord]:
        return self._active().list_spaces()

    def get(self, space_id: str) -> SpaceRecord | None:
        return self._active().get(space_id)

    def create(self, name: str) -> SpaceRecord:
        return self._active().create(name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._active(), name)

    def clear_for_tests(self) -> None:
        self._by_tenant.clear()


class TenantAudit:
    """Ask-audit rows are visible only to the company that recorded them.

    Rows recorded before a principal existed stay with the deployment tenant.
    """

    def __init__(self, deployment_tenant: str, inner: Any) -> None:
        self._deployment_tenant = deployment_tenant
        self._inner = inner
        self._owner: dict[str, str] = {}

    @property
    def backend(self) -> str:
        return self._inner.backend

    @property
    def dropped(self) -> int:
        return self._inner.dropped

    @dropped.setter
    def dropped(self, value: int) -> None:
        self._inner.dropped = value

    def record(self, rec: AskAuditRecord) -> None:
        self._owner[rec.ask_id] = bound_tenant_id(self._deployment_tenant)
        self._inner.record(rec)

    def list_between(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[AskAuditRecord]:
        rows = self._inner.list_between(since=since, until=until, limit=limit)
        tenant = bound_tenant_id(self._deployment_tenant)
        owned = []
        for row in rows:
            owner = self._owner.get(row.ask_id, self._deployment_tenant)
            if owner == tenant:
                owned.append(row)
        return owned

    def clear_for_tests(self) -> None:
        self._owner.clear()
        rows = getattr(self._inner, "_rows", None)
        if rows is not None:
            rows.clear()
            self._inner._size = 0
            self._inner.dropped = 0


def scope_stores(app: Any, deployment_tenant: str) -> None:
    """Wrap the live stores once. A second call after lifespan replaces them."""
    spaces = app.state.space_store
    if not isinstance(spaces, TenantSpaces):
        app.state.space_store = TenantSpaces(deployment_tenant, spaces)
    audit = app.state.ask_audit_store
    if not isinstance(audit, TenantAudit):
        app.state.ask_audit_store = TenantAudit(deployment_tenant, audit)
    if app not in _seen:
        _seen.append(app)


def clear_for_tests() -> None:
    for app in _seen:
        spaces = getattr(app.state, "space_store", None)
        audit = getattr(app.state, "ask_audit_store", None)
        if isinstance(spaces, TenantSpaces):
            spaces.clear_for_tests()
        if isinstance(audit, TenantAudit):
            audit.clear_for_tests()
