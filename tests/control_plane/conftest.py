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


def _connect_error() -> str | None:
    """None when Postgres answers; otherwise the reason it did not."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return None
    except Exception as exc:
        return str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__


_CONNECT_ERROR = _connect_error()

# R-0002: a skipped test is a failing test. These are the *only* proof that
# tenant isolation actually isolates — RLS, proposal single-apply, ledger
# pointers — and they used to disappear from the run whenever Postgres was
# absent, which on a fresh machine is always. The suite then reported green
# while never executing the thing it exists to check.
#
# Default is now a hard failure naming the cause. Genuinely have no Postgres?
# Say so out loud with DMS_SKIP_CONTROL_PLANE_TESTS=1, so the skip is a decision
# somebody made rather than a silence nobody noticed.
_SKIP_OPT_OUT = os.environ.get("DMS_SKIP_CONTROL_PLANE_TESTS", "").lower() in {"1", "true", "yes"}

pytestmark = pytest.mark.usefixtures("_require_postgres")


@pytest.fixture(scope="session", autouse=True)
def _require_postgres() -> None:
    """Session-scoped so it settles before ``migrated_db`` tries to connect.

    As a function-scoped fixture it ran *after* the session-scoped setup, so an
    unreachable Postgres produced ten fixture errors and a two-minute wait on
    connect retries instead of one clear answer.
    """
    if _CONNECT_ERROR is None:
        return
    if _SKIP_OPT_OUT:
        pytest.skip(f"DMS_SKIP_CONTROL_PLANE_TESTS set; Postgres unreachable ({_CONNECT_ERROR})")
    raise AssertionError(
        f"Control-plane tests need Postgres at {DATABASE_URL} but it is unreachable: "
        f"{_CONNECT_ERROR}\n"
        "Start it with:\n"
        "  docker compose -f deploy/compose/docker-compose.yml "
        "-f deploy/compose/docker-compose.hostdb.yml up -d postgres\n"
        "Or set DMS_SKIP_CONTROL_PLANE_TESTS=1 to skip deliberately."
    )


@pytest.fixture(scope="session")
def migrated_db(_require_postgres: None) -> Iterator[str]:
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
