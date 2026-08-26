"""SPACE-UI-ALL — amend proposal list must narrow when space_id is named.

``GET /v1/amend/proposals`` returned every Space's diffs for the tenant. The
UI Space switcher had nowhere to put a scope, so an active Space still showed
another Space's pending amends. Same shape as the runs feed leftover (#74).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from dms_core.control_plane.proposals import create_proposal_version
from dms_core.control_plane.session import set_tenant_context
from fastapi.testclient import TestClient


def _insert_proposal(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    space_id: uuid.UUID,
    summary: str,
) -> str:
    set_tenant_context(conn, tenant_id, role="steward")
    proposal_id = uuid.uuid4()
    conn.execute(
        """
        INSERT INTO dms.proposals (id, tenant_id, space_id, created_by)
        VALUES (%s, %s, %s, %s)
        """,
        (proposal_id, tenant_id, space_id, user_id),
    )
    create_proposal_version(
        conn,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        diff={"summary": summary},
    )
    return str(proposal_id)


@pytest.fixture
def amend_client(two_spaces: dict[str, str], conn: psycopg.Connection) -> Iterator[TestClient]:
    tenant = uuid.UUID(two_spaces["tenant_id"])
    set_tenant_context(conn, tenant, role="admin")
    user_id = conn.execute(
        "SELECT created_by FROM dms.spaces WHERE id = %s",
        (uuid.UUID(two_spaces["space_a"]),),
    ).fetchone()[0]
    _insert_proposal(
        conn,
        tenant_id=tenant,
        user_id=user_id,
        space_id=uuid.UUID(two_spaces["space_a"]),
        summary="fix in space A",
    )
    _insert_proposal(
        conn,
        tenant_id=tenant,
        user_id=user_id,
        space_id=uuid.UUID(two_spaces["space_b"]),
        summary="fix in space B",
    )
    conn.commit()

    prev_url = os.environ.get("DATABASE_URL")
    prev_tenant = os.environ.get("DMS_TENANT_ID")
    prev_actor = os.environ.get("DMS_ACTOR_USER_ID")
    os.environ["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL", "postgresql://dms:dms@127.0.0.1:5432/dms"
    )
    os.environ["DMS_TENANT_ID"] = str(tenant)
    os.environ["DMS_ACTOR_USER_ID"] = str(user_id)
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    from dms_api.app import create_app

    yield TestClient(create_app())

    get_settings.cache_clear()
    if prev_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = prev_url
    if prev_tenant is None:
        os.environ.pop("DMS_TENANT_ID", None)
    else:
        os.environ["DMS_TENANT_ID"] = prev_tenant
    if prev_actor is None:
        os.environ.pop("DMS_ACTOR_USER_ID", None)
    else:
        os.environ["DMS_ACTOR_USER_ID"] = prev_actor
    get_settings.cache_clear()


def test_amend_list_scopes_to_named_space(
    amend_client: TestClient, two_spaces: dict[str, str]
) -> None:
    unscoped = amend_client.get("/v1/amend/proposals").json()
    assert len(unscoped) >= 2, "fixture must hold one proposal per Space"

    a = amend_client.get(
        "/v1/amend/proposals", params={"space_id": two_spaces["space_a"]}
    ).json()
    b = amend_client.get(
        "/v1/amend/proposals", params={"space_id": two_spaces["space_b"]}
    ).json()

    a_ids = {row["space_id"] for row in a}
    b_ids = {row["space_id"] for row in b}
    assert a_ids == {two_spaces["space_a"]}, f"A scoped list leaked: {a}"
    assert b_ids == {two_spaces["space_b"]}, f"B scoped list leaked: {b}"
    assert {row["id"] for row in a}.isdisjoint({row["id"] for row in b})
