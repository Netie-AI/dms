"""PostgresSpaceStore: creating a Space actually persists, and stays tenant-scoped.

The store shipped with ``list_spaces`` and ``get`` but no ``create``, while the
route reached for it through ``getattr(store, "create", None)`` and answered 501
when it was missing. So ``POST /v1/spaces`` worked on the memory store and would
have started failing on the first write after DATABASE_URL was set — the moment
the feature was supposed to start working. These tests are the guard for that.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from dms_core.control_plane.pg_spaces import PostgresSpaceStore
from dms_core.control_plane.session import set_tenant_context
from dms_core.control_plane.spaces import SpaceStorePort

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.fixture
def tenant(conn: psycopg.Connection, two_tenants: dict) -> uuid.UUID:
    return two_tenants["alpha"]


def _store(migrated_db: str, tenant_id: uuid.UUID) -> PostgresSpaceStore:
    return PostgresSpaceStore(migrated_db, tenant_id=tenant_id, role="steward")


def test_postgres_store_satisfies_the_port() -> None:
    """The 501 existed because the port did not require create(). It does now."""
    assert isinstance(PostgresSpaceStore, type)
    assert hasattr(PostgresSpaceStore, "create")
    assert issubclass(PostgresSpaceStore, SpaceStorePort)


def test_create_persists_and_is_listed(migrated_db: str, tenant: uuid.UUID) -> None:
    store = _store(migrated_db, tenant)
    name = f"Pilot {uuid.uuid4().hex[:6]}"

    record = store.create(name)

    assert record.name == name
    assert record.member_count == 1, "creator should be seeded as the first member"
    # A separate store instance means a separate connection: this is persistence,
    # not an object still held in memory by the caller.
    assert any(s.id == record.id for s in _store(migrated_db, tenant).list_spaces())
    assert _store(migrated_db, tenant).get(record.id) is not None


def test_duplicate_name_raises_the_value_error_the_route_maps_to_409(
    migrated_db: str, tenant: uuid.UUID
) -> None:
    store = _store(migrated_db, tenant)
    name = f"Duplicate {uuid.uuid4().hex[:6]}"
    store.create(name)

    with pytest.raises(ValueError, match="space_name_taken"):
        store.create(name)


def test_created_space_is_invisible_to_another_tenant(
    migrated_db: str, two_tenants: dict, conn: psycopg.Connection
) -> None:
    """The write lands under RLS, so the isolation covers created rows too."""
    alpha, beta = two_tenants["alpha"], two_tenants["beta"]
    record = _store(migrated_db, alpha).create(f"Alpha only {uuid.uuid4().hex[:6]}")

    assert all(s.id != record.id for s in _store(migrated_db, beta).list_spaces())
    assert _store(migrated_db, beta).get(record.id) is None

    set_tenant_context(conn, beta, role="admin")
    assert (
        conn.execute(
            "SELECT count(*) FROM dms.spaces WHERE id::text = %s", (record.id,)
        ).fetchone()[0]
        == 0
    )


def test_blank_name_is_refused(migrated_db: str, tenant: uuid.UUID) -> None:
    with pytest.raises(ValueError, match="space_name_required"):
        _store(migrated_db, tenant).create("   ")
