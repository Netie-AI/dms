"""STUDIO-FIX-A on Postgres. The memory fixture never calls UUID.

A Space that exists only in Postgres must answer 200 with its own sources.
A second Space's source must not appear. A missing Space is 404
``space_not_found``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.control_plane.session import set_tenant_context
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("migrated_db")


@pytest.fixture
def scoped_client(
    migrated_db: str,
    two_tenants: dict[str, uuid.UUID],
    conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, str]]]:
    tenant = two_tenants["alpha"]
    user = two_tenants["user"]
    space_a = uuid.uuid4()
    space_b = uuid.uuid4()
    ref_a = f"studio-fix-a-{space_a.hex[:8]}"
    ref_b = f"studio-fix-b-{space_b.hex[:8]}"
    set_tenant_context(conn, tenant, role="admin")
    for sid, name in ((space_a, f"fix-a-{space_a.hex[:6]}"), (space_b, f"fix-b-{space_b.hex[:6]}")):
        conn.execute(
            """
            INSERT INTO dms.spaces (id, tenant_id, name, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (sid, tenant, name, user),
        )
    conn.execute(
        """
        INSERT INTO dms.data_sources (tenant_id, space_id, kind, ref, scope, meta)
        VALUES (%s, %s, 'csv', %s, 'team', '{}'::jsonb),
               (%s, %s, 'csv', %s, 'team', '{}'::jsonb)
        """,
        (tenant, space_a, ref_a, tenant, space_b, ref_b),
    )
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DMS_TENANT_ID", str(tenant))
    monkeypatch.setenv("DMS_ACTOR_USER_ID", str(user))
    get_settings.cache_clear()
    ids = {"a": str(space_a), "b": str(space_b), "ref_a": ref_a, "ref_b": ref_b}
    with TestClient(create_app()) as client:
        yield client, ids
    get_settings.cache_clear()


def test_postgres_space_sources_are_scoped(
    scoped_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, ids = scoped_client
    one = client.get(f"/v1/spaces/{ids['a']}/sources")
    other = client.get(f"/v1/spaces/{ids['b']}/sources")
    missing = client.get(f"/v1/spaces/{uuid.uuid4()}/sources")

    assert one.status_code == 200
    body = one.json()
    assert body["space_id"] == ids["a"]
    assert body["count"] == len(body["sources"])
    assert body["count"] >= 1
    assert all(row["space_id"] == ids["a"] for row in body["sources"])
    assert {row["ref"] for row in body["sources"]} == {ids["ref_a"]}
    assert ids["ref_b"] not in {row["ref"] for row in body["sources"]}

    assert other.status_code == 200
    other_body = other.json()
    assert other_body["count"] == len(other_body["sources"])
    assert all(row["space_id"] == ids["b"] for row in other_body["sources"])
    assert {row["id"] for row in body["sources"]}.isdisjoint(
        {row["id"] for row in other_body["sources"]}
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "space_not_found"
