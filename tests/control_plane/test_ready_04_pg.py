"""READY-04 on Postgres. A new tenant insert used to die as HTTP 500.

The missing user foreign key is 409 when the flag is on, and the create
route inserts the user before the Space so the key holds. Flag off, the
route is absent and that foreign key stays a bare 500.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from cortex_client.gate import ComplianceDecision
from dms_core.control_plane.ready_flow import reset as reset_ready
from dms_core.control_plane.session import set_tenant_context

pytestmark = pytest.mark.usefixtures("migrated_db")


def _allow(**kwargs):
    return ComplianceDecision(allowed=True, reason="ok", action=str(kwargs.get("action") or ""))


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch, migrated_db: str):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.delenv("DMS_READY_TENANT", raising=False)
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    yield migrated_db
    reset_ready()
    get_settings.cache_clear()


def _client(monkeypatch: pytest.MonkeyPatch, tenant_id: str) -> object:
    monkeypatch.setenv("DMS_TENANT_ID", tenant_id)
    monkeypatch.setenv("DMS_READY_TENANT", "1")
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("dms_api.routes.ready.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.spaces.compliance_gate", _allow)
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    return TestClient(create_app(), raise_server_exceptions=False)


def test_create_tenant_is_not_500(
    db_env: str, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.uuid4()
    name = f"Synthetic {tenant.hex[:8]}"
    with _client(monkeypatch, str(tenant)) as client:
        res = client.post("/v1/ready/tenants", json={"name": name})
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["persisted"] is True
        assert body["created_user"] is True
        assert body["tenant_id"] == str(tenant)
        assert "password" not in res.text.lower()
        again = client.post("/v1/spaces", json={"name": f"Second {tenant.hex[:8]}"})
    assert again.status_code == 201, again.text
    conn.rollback()
    assert conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (tenant,)).fetchone() is not None
    assert (
        conn.execute("SELECT 1 FROM dms.memberships WHERE tenant_id = %s", (tenant,)).fetchone()
        is not None
    )
    assert (
        conn.execute(
            "SELECT 1 FROM dms.spaces WHERE tenant_id = %s AND created_by IS NOT NULL",
            (tenant,),
        ).fetchone()
        is not None
    )


def test_flag_off_route_is_absent(
    db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_READY_TENANT", raising=False)
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    get_settings.cache_clear()
    client = TestClient(create_app())
    res = client.post("/v1/ready/tenants", json={"name": "Synthetic Pharmacy"})
    assert res.status_code == 404


def test_actor_fk_is_409_only_when_the_flag_is_on(
    conn: psycopg.Connection, two_tenants: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_api.tenant_http import response_for_foreign_key

    alpha = two_tenants["alpha"]
    set_tenant_context(conn, alpha, role="steward")
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as other_fk:
        conn.execute(
            "INSERT INTO dms.spaces (tenant_id, name, created_by) VALUES (%s, %s, %s)",
            (alpha, f"orphan-{uuid.uuid4().hex[:8]}", uuid.uuid4()),
        )
    conn.rollback()
    monkeypatch.delenv("DMS_READY_TENANT", raising=False)
    bare = response_for_foreign_key(other_fk.value)
    assert bare.status_code == 500
    monkeypatch.setenv("DMS_READY_TENANT", "1")
    typed = response_for_foreign_key(other_fk.value)
    assert typed.status_code == 409
    assert typed.body == b'{"detail":{"code":"actor_not_in_tenant"}}'
