"""Control-plane test fixtures — require Postgres (compose postgres service)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from alembic.config import Config

from alembic import command

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:5432/dms")


def _can_connect() -> bool:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _can_connect(), reason="Postgres not available")


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cfg = Config(os.path.join(root, "alembic.ini"))
    os.environ["DATABASE_URL"] = DATABASE_URL
    # Reset schema for a clean session
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS dms CASCADE")
        # Drop app roles if present so upgrade is idempotent on roles
        for role in ("dms_viewer", "dms_steward", "dms_admin"):
            conn.execute(f"DROP ROLE IF EXISTS {role}")
    command.upgrade(cfg, "head")
    yield DATABASE_URL


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(migrated_db) as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def two_tenants(conn: psycopg.Connection) -> dict[str, uuid.UUID]:
    """Seed two tenants, a user, and a space in each."""
    from dms_core.control_plane.session import set_tenant_context

    t_a = uuid.uuid4()
    t_b = uuid.uuid4()
    user_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    conn.execute(
        "INSERT INTO dms.tenants (id, slug, name) VALUES (%s, %s, 'Alpha'), (%s, %s, 'Beta')",
        (t_a, f"alpha-{suffix}", t_b, f"beta-{suffix}"),
    )
    conn.execute(
        "INSERT INTO dms.users (id, email, display_name) VALUES (%s, %s, 'Tester')",
        (user_id, f"tester-{suffix}@example.com"),
    )
    role_steward = conn.execute(
        "SELECT id FROM dms.roles WHERE name = 'steward'"
    ).fetchone()
    assert role_steward is not None
    steward_id = role_steward[0]

    for tid, slug in ((t_a, "a"), (t_b, "b")):
        set_tenant_context(conn, tid, role="admin")
        conn.execute(
            """
            INSERT INTO dms.memberships (tenant_id, user_id, role_id)
            VALUES (%s, %s, %s)
            """,
            (tid, user_id, steward_id),
        )
        conn.execute(
            """
            INSERT INTO dms.spaces (id, tenant_id, name, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid.uuid4(), tid, f"space-{slug}-{suffix}", user_id),
        )

    conn.commit()
    return {"alpha": t_a, "beta": t_b, "user": user_id, "steward_role": steward_id}
