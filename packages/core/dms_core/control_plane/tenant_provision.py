"""Missing-tenant foreign key, and the operator provision command.

A write whose ``tenant_id`` is not in ``dms.tenants`` raises Postgres
``ForeignKeyViolation`` on ``<table>_tenant_id_fkey``. That is a missing
tenant, not a crash. The API maps it to the code ``tenant_not_provisioned``.

Provisioning is a separate operator command (``DMS_TENANT_PROVISION=1``).
The write path never inserts the tenant row.
"""

from __future__ import annotations

import os
import re
from typing import Any
from uuid import UUID

import psycopg
import psycopg.errors

from dms_core.control_plane.session import set_tenant_context

TENANT_NOT_PROVISIONED = "tenant_not_provisioned"
FLAG = "DMS_TENANT_PROVISION"
DEFAULT_POOL = "default"
_STEWARD_LOGIN = "dms_steward"
_TENANT_FK_SUFFIX = "_tenant_id_fkey"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_NAME_MAX = 200


def provision_enabled() -> bool:
    """True only when ``DMS_TENANT_PROVISION`` is the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


def is_missing_tenant_constraint(name: object) -> bool:
    """True for the default Postgres name of a ``tenant_id`` foreign key."""
    return (
        isinstance(name, str)
        and len(name) > len(_TENANT_FK_SUFFIX)
        and name.endswith(_TENANT_FK_SUFFIX)
    )


def is_missing_tenant_fk(exc: BaseException) -> bool:
    """True when ``exc`` or its cause is the missing-tenant foreign key.

    The driver text is not read. It can quote the statement, and the HTTP
    body must not.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, psycopg.errors.ForeignKeyViolation):
            diag = cur.diag
            name = diag.constraint_name if diag is not None else None
            if is_missing_tenant_constraint(name):
                return True
        cause = cur.__cause__
        cur = cause if isinstance(cause, BaseException) else None
    return False


class TenantProvisionError(Exception):
    """Operator-facing refusal. ``code`` is the only text a caller may print."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _tenant_id(raw: str) -> str:
    try:
        return str(UUID(str(raw).strip()))
    except (ValueError, AttributeError, TypeError):
        raise TenantProvisionError("tenant_id_invalid") from None


def _name(raw: str) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise TenantProvisionError("tenant_name_required")
    if len(text) > _NAME_MAX:
        raise TenantProvisionError("tenant_name_too_long")
    return text


def _slug(raw: str | None, tenant: str) -> str:
    if raw is None:
        slug = "t-" + tenant
    else:
        slug = raw.strip() if isinstance(raw, str) else ""
    if not _SLUG.fullmatch(slug):
        raise TenantProvisionError("tenant_slug_invalid")
    return slug


def _constraint_name(exc: BaseException) -> str:
    diag = getattr(exc, "diag", None)
    name = getattr(diag, "constraint_name", None) if diag is not None else None
    return name if isinstance(name, str) else ""


def provision_tenant(
    conninfo: str,
    tenant_id: str,
    name: str,
    *,
    slug: str | None = None,
) -> dict[str, Any]:
    """Insert the tenant row and its default pool. Idempotent. Steward role only.

    The tenant insert runs as the database owner: ``dms_steward`` has no INSERT
    on ``dms.tenants``. The pool insert runs after ``SET ROLE dms_steward``.
    This never sets the admin role, and it never writes a user, a membership,
    a key, a grant, or a credential. Flag off raises before a connection opens.
    """
    if not provision_enabled():
        raise TenantProvisionError("tenant_provision_disabled")
    if not isinstance(conninfo, str) or not conninfo.strip():
        raise TenantProvisionError("database_url_unset")
    tenant = _tenant_id(tenant_id)
    clean_name = _name(name)
    clean_slug = _slug(slug, tenant)
    inserted = None
    pool = None
    try:
        with psycopg.connect(conninfo, connect_timeout=5) as conn:
            inserted = conn.execute(
                """
                INSERT INTO dms.tenants (id, slug, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                (tenant, clean_slug, clean_name),
            ).fetchone()
            set_tenant_context(conn, tenant, role="steward")
            who = conn.execute("SELECT current_user").fetchone()
            if who is None or str(who[0]) != _STEWARD_LOGIN:
                raise TenantProvisionError("tenant_provision_role_refused")
            pool = conn.execute(
                """
                INSERT INTO dms.compute_pool (tenant_id, name, kind)
                VALUES (%s, %s, 'duckdb')
                ON CONFLICT (tenant_id, name) DO NOTHING
                RETURNING id
                """,
                (tenant, DEFAULT_POOL),
            ).fetchone()
            conn.commit()
    except TenantProvisionError:
        raise
    except psycopg.errors.UniqueViolation as exc:
        if _constraint_name(exc).endswith("_slug_key"):
            raise TenantProvisionError("tenant_slug_taken") from None
        raise TenantProvisionError("tenant_provision_failed") from None
    except psycopg.Error:
        raise TenantProvisionError("tenant_provision_failed") from None
    return {
        "tenant_id": tenant,
        "pool": DEFAULT_POOL,
        "role": _STEWARD_LOGIN,
        "created_tenant": inserted is not None,
        "created_pool": pool is not None,
    }
