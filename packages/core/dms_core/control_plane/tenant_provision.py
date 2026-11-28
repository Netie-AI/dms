"""Missing-tenant foreign key, and the operator provision command.

A write whose ``tenant_id`` is not in ``dms.tenants`` raises Postgres
``ForeignKeyViolation`` on ``<table>_tenant_id_fkey``. That is a missing
tenant, not a crash. The API maps it to the code ``tenant_not_provisioned``.

Provisioning is a separate operator command (``DMS_TENANT_PROVISION=1``).
The write path never inserts the tenant row.
"""

from __future__ import annotations

import os

import psycopg.errors

TENANT_NOT_PROVISIONED = "tenant_not_provisioned"
FLAG = "DMS_TENANT_PROVISION"
_TENANT_FK_SUFFIX = "_tenant_id_fkey"


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
