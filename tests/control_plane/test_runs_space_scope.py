"""A0007-03 (#74) - the runs feed must be scopeable, and gated before it reads.

`GET /v1/runs` filtered on `tenant_id` alone and joined `dms.spaces` only to read
a display name. It had **no `space_id` parameter at all**, so one call returned
every Space's runs and the caller could not have narrowed it if they wanted to.
That is not a skippable check like the Library previews (A-0007-01) - it is an
absent one.

Measured on a configured control plane before the fix: two Spaces holding one
ingest run each, one request, both runs returned.

These assert on the HTTP response body, never on the SQL (CLAUDE.md hard rule 10)
and never offline - with no DATABASE_URL the route short-circuits to
`configured: false` and an empty list, which would let this file pass while
proving nothing.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from dms_core.control_plane.session import set_tenant_context
from fastapi.testclient import TestClient


@pytest.fixture
def runs_client(two_spaces: dict[str, str], conn: psycopg.Connection) -> Iterator[TestClient]:
    """Two Spaces, one succeeded ingest run each, served by a configured API."""
    tenant = uuid.UUID(two_spaces["tenant_id"])
    set_tenant_context(conn, tenant, role="admin")
    user_id = conn.execute(
        "SELECT created_by FROM dms.spaces WHERE id = %s", (uuid.UUID(two_spaces["space_a"]),)
    ).fetchone()[0]
    for key in ("space_a", "space_b"):
        conn.execute(
            """
            INSERT INTO dms.ingest_run (id, tenant_id, space_id, status, receipt, created_by)
            VALUES (%s, %s, %s, 'succeeded', %s, %s)
            """,
            (
                uuid.uuid4(),
                tenant,
                uuid.UUID(two_spaces[key]),
                json.dumps({"files_seen": 1, "ingested": 1, "note": f"run in {key}"}),
                user_id,
            ),
        )
    conn.commit()

    prev_url = os.environ.get("DATABASE_URL")
    prev_tenant = os.environ.get("DMS_TENANT_ID")
    os.environ["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL", "postgresql://dms:dms@127.0.0.1:5432/dms"
    )
    os.environ["DMS_TENANT_ID"] = str(tenant)
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
    get_settings.cache_clear()


def _spaces_in(body: dict) -> set[str]:
    return {r.get("space_name") for r in (body.get("runs") or []) if r.get("space_name")}


def test_the_feed_can_be_scoped_to_one_space(
    runs_client: TestClient, two_spaces: dict[str, str]
) -> None:
    """The property that did not exist: naming a Space narrows the feed.

    Before the fix there was no parameter to name one with, so this could not
    have been written at all.
    """
    resp = runs_client.get("/v1/runs", params={"space_id": two_spaces["space_a"]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True, "offline run proves nothing - see module docstring"
    names = _spaces_in(body)
    assert len(names) == 1, f"a scoped feed returned {len(names)} Spaces: {sorted(names)}"


def test_a_scoped_feed_does_not_carry_another_spaces_run(
    runs_client: TestClient, two_spaces: dict[str, str]
) -> None:
    """The leak itself, asserted on the body.

    Both Spaces hold exactly one run, so a scoped call returning two is the
    defect and a scoped call returning the *other* Space's run is the defect.
    """
    unscoped = runs_client.get("/v1/runs").json()
    assert len(_spaces_in(unscoped)) == 2, (
        "fixture is not exercising the leak - expected two Spaces with runs"
    )

    scoped = runs_client.get("/v1/runs", params={"space_id": two_spaces["space_b"]}).json()
    b_names = _spaces_in(scoped)
    a_names = _spaces_in(runs_client.get(
        "/v1/runs", params={"space_id": two_spaces["space_a"]}
    ).json())

    assert b_names.isdisjoint(a_names), (
        f"the two scopes overlap: A={sorted(a_names)} B={sorted(b_names)}"
    )


def test_the_feed_names_the_scope_it_applied(
    runs_client: TestClient, two_spaces: dict[str, str]
) -> None:
    """A feed that silently widened is the failure - so it says which scope ran."""
    assert runs_client.get("/v1/runs").json()["scope"] == "all-spaces"
    scoped = runs_client.get("/v1/runs", params={"space_id": two_spaces["space_a"]}).json()
    assert scoped["scope"] == f"space:{two_spaces['space_a']}"


def test_an_unreachable_gate_does_not_refuse_the_read(runs_client: TestClient) -> None:
    """R-0005 - these are reads, so `enforce(mutation=False)`.

    Cortex is not running in this suite, so the gate is unreachable on every
    call above. Without the read posture all three routes would 403 here and the
    Runs page would go dark whenever the engine restarts - a control refusing
    legitimate work, not a boundary holding.
    """
    resp = runs_client.get("/v1/runs")

    assert resp.status_code == 200, (
        f"gate-unreachable refused a read: {resp.status_code} {resp.text[:200]}"
    )
    assert resp.json()["configured"] is True
