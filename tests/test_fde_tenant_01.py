"""FDE-TENANT-01. A missing tenant is a typed 409, not a 500.

The code is ``tenant_not_provisioned``. The body does not carry the driver
text. The operator CLI is off unless ``DMS_TENANT_PROVISION`` is 1, and off
it does not open a database connection.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import psycopg.errors
import pytest
from dms_core.control_plane.tenant_provision import (
    TENANT_NOT_PROVISIONED,
    TenantProvisionError,
    is_missing_tenant_constraint,
    is_missing_tenant_fk,
    provision_enabled,
    provision_tenant,
)

ROOT = Path(__file__).resolve().parents[1]


def test_constraint_name_is_the_tenant_foreign_key_only() -> None:
    assert is_missing_tenant_constraint("spaces_tenant_id_fkey")
    assert is_missing_tenant_constraint("proposals_tenant_id_fkey")
    assert is_missing_tenant_constraint("compute_pool_tenant_id_fkey")
    assert not is_missing_tenant_constraint("spaces_created_by_fkey")
    assert not is_missing_tenant_constraint("tenants_pkey")
    assert not is_missing_tenant_constraint("_tenant_id_fkey")
    assert not is_missing_tenant_constraint(None)
    assert not is_missing_tenant_constraint("")
    assert not is_missing_tenant_constraint(409)


def test_a_non_fk_is_not_a_missing_tenant() -> None:
    assert is_missing_tenant_fk(RuntimeError("insert into dms.spaces")) is False


def test_provision_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("DMS_TENANT_PROVISION", raising=False)
    assert provision_enabled() is False
    for raw in ("", "0", "true", "yes", "on", "2"):
        monkeypatch.setenv("DMS_TENANT_PROVISION", raw)
        assert provision_enabled() is False
    monkeypatch.setenv("DMS_TENANT_PROVISION", "1")
    assert provision_enabled() is True


def test_handler_body_is_the_code_only() -> None:
    from dms_api.tenant_http import STATUS, response_for_foreign_key, tenant_not_provisioned_body

    body = tenant_not_provisioned_body()
    assert body == {"detail": {"code": TENANT_NOT_PROVISIONED}}
    assert STATUS == 409
    rendered = str(body)
    for leaked in ("INSERT", "SELECT", "fkey", "violates", "psycopg"):
        assert leaked not in rendered
    other = response_for_foreign_key(RuntimeError("SELECT 1"))
    assert other.status_code == 500
    assert other.body == b'{"detail":"Internal Server Error"}'


def test_the_foreign_key_handler_is_installed() -> None:
    from dms_api.app import create_app

    app = create_app()
    assert psycopg.errors.ForeignKeyViolation in app.exception_handlers


def _cli():
    path = ROOT / "scripts" / "ops" / "provision_tenant.py"
    spec = importlib.util.spec_from_file_location("provision_tenant_cli", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flag_off_refuses_before_connect(monkeypatch, capsys) -> None:
    """Flag off is byte-identical: the command writes nothing and does not connect."""
    monkeypatch.delenv("DMS_TENANT_PROVISION", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid")

    def boom(*_a, **_k):
        raise AssertionError("connect")

    monkeypatch.setattr("dms_core.control_plane.tenant_provision.psycopg.connect", boom)
    with pytest.raises(TenantProvisionError) as exc:
        provision_tenant("postgresql://invalid", str(uuid4()), "Synthetic Pharmacy")
    assert exc.value.code == "tenant_provision_disabled"

    code = _cli().main(["--tenant-id", str(uuid4()), "--name", "Synthetic Pharmacy"])
    assert code == 2
    assert capsys.readouterr().err.strip() == "tenant_provision_disabled"


def test_flag_on_still_refuses_a_bad_id_before_connect(monkeypatch) -> None:
    monkeypatch.setenv("DMS_TENANT_PROVISION", "1")

    def boom(*_a, **_k):
        raise AssertionError("connect")

    monkeypatch.setattr("dms_core.control_plane.tenant_provision.psycopg.connect", boom)
    with pytest.raises(TenantProvisionError) as missing:
        provision_tenant("", str(uuid4()), "Synthetic Pharmacy")
    assert missing.value.code == "database_url_unset"
    with pytest.raises(TenantProvisionError) as bad:
        provision_tenant("postgresql://invalid", "not-a-uuid", "Synthetic Pharmacy")
    assert bad.value.code == "tenant_id_invalid"


def test_provision_inserts_only_the_tenant_and_the_pool() -> None:
    path = ROOT / "packages" / "core" / "dms_core" / "control_plane" / "tenant_provision.py"
    text = path.read_text(encoding="utf-8")
    assert text.count("INSERT INTO") == 2
    assert "INSERT INTO dms.tenants" in text
    assert "INSERT INTO dms.compute_pool" in text
    assert 'role="steward"' in text
    assert 'role="admin"' not in text
    assert "dms_admin" not in text
