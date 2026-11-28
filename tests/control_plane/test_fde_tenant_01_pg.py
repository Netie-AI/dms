"""FDE-TENANT-01. Unknown tenant is HTTP 409, and the CLI is idempotent.

Synthetic ids only. The schema is the control-plane fixture, not a customer
database. Flag off, the CLI writes nothing. Flag on, a write route still
does not create the tenant: that stays the operator command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import psycopg.errors
import pytest
from cortex_client.gate import ComplianceDecision
from dms_core.control_plane.session import set_tenant_context
from dms_core.control_plane.tenant_provision import is_missing_tenant_fk
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("migrated_db")

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "ops" / "provision_tenant.py"


def _allow(**kwargs):
    return ComplianceDecision(allowed=True, reason="ok", action=str(kwargs.get("action") or ""))


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch, migrated_db: str):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.delenv("DMS_TENANT_PROVISION", raising=False)
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    yield migrated_db
    get_settings.cache_clear()


def _client(
    monkeypatch: pytest.MonkeyPatch, tenant_id: str, *, actor: str | None = None
) -> TestClient:
    monkeypatch.setenv("DMS_TENANT_ID", tenant_id)
    if actor is not None:
        monkeypatch.setenv("DMS_ACTOR_USER_ID", actor)
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("dms_api.routes.spaces.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.amend.compliance_gate", _allow)
    from dms_api.app import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _run_cli(migrated_db: str, *args: str, flag: str | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = migrated_db
    if flag is None:
        env.pop("DMS_TENANT_PROVISION", None)
    else:
        env["DMS_TENANT_PROVISION"] = flag
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_unknown_tenant_post_spaces_is_typed_4xx(
    db_env: str, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = uuid.uuid4()
    with _client(monkeypatch, str(unknown)) as client:
        res = client.post("/v1/spaces", json={"name": "Synthetic Pharmacy"})
    assert res.status_code == 409
    assert res.json() == {"detail": {"code": "tenant_not_provisioned"}}
    raw = res.text.lower()
    for leaked in ("insert", "select", "fkey", "violates", "psycopg"):
        assert leaked not in raw
    conn.rollback()
    assert conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (unknown,)).fetchone() is None
    assert (
        conn.execute("SELECT 1 FROM dms.spaces WHERE tenant_id = %s", (unknown,)).fetchone() is None
    )


def test_flag_on_does_not_provision_on_the_write(
    db_env: str, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag arms the operator CLI only. A request must not create the tenant."""
    monkeypatch.setenv("DMS_TENANT_PROVISION", "1")
    unknown = uuid.uuid4()
    with _client(monkeypatch, str(unknown)) as client:
        res = client.post("/v1/spaces", json={"name": "Synthetic Pharmacy"})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "tenant_not_provisioned"
    conn.rollback()
    assert conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (unknown,)).fetchone() is None


def test_known_tenant_space_still_creates(
    db_env: str,
    conn: psycopg.Connection,
    two_tenants: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = two_tenants["alpha"]
    name = f"Synthetic {uuid.uuid4().hex[:8]}"
    with _client(monkeypatch, str(tenant)) as client:
        res = client.post("/v1/spaces", json={"name": name})
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["space"]["name"] == name
        assert body["persisted"] is True
        again = client.post("/v1/spaces", json={"name": name})
    assert again.status_code == 409
    assert again.json()["detail"] == "space_name_taken"
    conn.rollback()
    assert (
        conn.execute(
            "SELECT count(*) FROM dms.spaces WHERE tenant_id = %s AND name = %s",
            (tenant, name),
        ).fetchone()[0]
        == 1
    )


def test_unknown_tenant_proposal_is_the_same_code(
    db_env: str, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = uuid.uuid4()
    actor = uuid.uuid4()
    conn.execute(
        "INSERT INTO dms.users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
        (actor, f"synthetic-{actor.hex[:8]}@example.com"),
    )
    conn.commit()
    with _client(monkeypatch, str(unknown), actor=str(actor)) as client:
        res = client.post("/v1/amend/proposals", json={"diff": {}, "summary": ""})
    assert res.status_code == 409, res.text
    assert res.json() == {"detail": {"code": "tenant_not_provisioned"}}
    assert "insert" not in res.text.lower()


def test_driver_fk_classification(conn: psycopg.Connection, two_tenants: dict) -> None:
    """The tenant foreign key is the 409. A different foreign key stays a bare 500."""
    from dms_api.tenant_http import response_for_foreign_key

    missing = uuid.uuid4()
    set_tenant_context(conn, missing, role="steward")
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as tenant_fk:
        conn.execute(
            "INSERT INTO dms.spaces (tenant_id, name) VALUES (%s, %s)",
            (missing, f"missing-{missing.hex[:8]}"),
        )
    conn.rollback()
    assert is_missing_tenant_fk(tenant_fk.value) is True
    mapped = response_for_foreign_key(tenant_fk.value)
    assert mapped.status_code == 409
    assert mapped.body == b'{"detail":{"code":"tenant_not_provisioned"}}'

    alpha = two_tenants["alpha"]
    set_tenant_context(conn, alpha, role="steward")
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as other_fk:
        conn.execute(
            "INSERT INTO dms.spaces (tenant_id, name, created_by) VALUES (%s, %s, %s)",
            (alpha, f"orphan-{uuid.uuid4().hex[:8]}", uuid.uuid4()),
        )
    conn.rollback()
    assert is_missing_tenant_fk(other_fk.value) is False
    bare = response_for_foreign_key(other_fk.value)
    assert bare.status_code == 500
    assert bare.body == b'{"detail":"Internal Server Error"}'


def test_cli_creates_then_is_a_noop(db_env: str, conn: psycopg.Connection) -> None:
    tenant = uuid.uuid4()
    args = ("--tenant-id", str(tenant), "--name", "Synthetic Pharmacy")
    first = _run_cli(db_env, *args, flag="1")
    assert first.returncode == 0, first.stderr
    assert "insert" not in first.stderr.lower()
    created = json.loads(first.stdout)
    assert created["created_tenant"] is True
    assert created["created_pool"] is True
    assert created["role"] == "dms_steward"
    assert created["pool"] == "default"

    second = _run_cli(db_env, *args, flag="1")
    assert second.returncode == 0, second.stderr
    again = json.loads(second.stdout)
    assert again["created_tenant"] is False
    assert again["created_pool"] is False

    conn.rollback()
    tenants = conn.execute("SELECT count(*) FROM dms.tenants WHERE id = %s", (tenant,)).fetchone()
    pools = conn.execute(
        "SELECT name, kind, config::text, id FROM dms.compute_pool WHERE tenant_id = %s",
        (tenant,),
    ).fetchall()
    assert tenants is not None and int(tenants[0]) == 1
    assert len(pools) == 1
    assert pools[0][0] == "default"
    assert pools[0][1] == "duckdb"
    assert pools[0][2] == "{}"
    for table in ("api_keys", "acl_grants", "memberships", "sessions", "spaces"):
        row = conn.execute(
            f"SELECT count(*) FROM dms.{table} WHERE tenant_id = %s", (tenant,)
        ).fetchone()
        assert row is not None and int(row[0]) == 0


def test_cli_flag_off_refuses_and_writes_nothing(db_env: str, conn: psycopg.Connection) -> None:
    tenant = uuid.uuid4()
    before = conn.execute("SELECT count(*) FROM dms.tenants").fetchone()
    conn.rollback()
    proc = _run_cli(
        db_env, "--tenant-id", str(tenant), "--name", "Synthetic Pharmacy", flag=None
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() == "tenant_provision_disabled"
    assert "://" not in proc.stderr
    conn.rollback()
    after = conn.execute("SELECT count(*) FROM dms.tenants").fetchone()
    assert before == after
    assert conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (tenant,)).fetchone() is None


def test_cli_slug_taken_writes_no_second_tenant(db_env: str, conn: psycopg.Connection) -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    ok = _run_cli(
        db_env,
        "--tenant-id",
        str(first),
        "--name",
        "Synthetic A",
        "--slug",
        f"synth-{first.hex[:8]}",
        flag="1",
    )
    assert ok.returncode == 0, ok.stderr
    slug = f"synth-{first.hex[:8]}"
    taken = _run_cli(
        db_env,
        "--tenant-id",
        str(second),
        "--name",
        "Synthetic B",
        "--slug",
        slug,
        flag="1",
    )
    assert taken.returncode == 2
    assert taken.stderr.strip() == "tenant_slug_taken"
    assert "insert" not in taken.stderr.lower()
    conn.rollback()
    assert conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (second,)).fetchone() is None
