"""FDE-TENANT-01. A missing tenant is a typed 409, not a 500.

The code is ``tenant_not_provisioned``. The body does not carry the driver
text. Provisioning is a later operator command and is not this module.
"""

from __future__ import annotations

import psycopg.errors
from dms_core.control_plane.tenant_provision import (
    TENANT_NOT_PROVISIONED,
    is_missing_tenant_constraint,
    is_missing_tenant_fk,
    provision_enabled,
)


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
