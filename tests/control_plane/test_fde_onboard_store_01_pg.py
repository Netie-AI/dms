"""FDE-ONBOARD-01 (dms#541) item store against Postgres.

The CI-runnable twin (``tests/test_fde_onboard_store_01.py``) runs the clauses
on the in-memory store. This one turns ``DMS_ONTO_PG`` on over a migrated
database (alembic 0010 widens the kind CHECK) and asserts the same clauses.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from dms_core.control_plane.onto_data import (
    ONBOARD_SOURCE,
    PostgresOntologyDataStore,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_core.control_plane.pg_spaces import PostgresSpaceStore
from dms_core.control_plane.session import set_tenant_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fde_onboard_store_01 import RUN, sample_items  # noqa: E402
from test_onto_derive_01 import make_minter  # noqa: E402
from test_onto_profile_01 import Rig, _approve, _revoke, make_rig  # noqa: E402


@pytest.fixture()
def pg_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrated_db: str,
    two_tenants: dict[str, Any],
) -> Iterator[tuple[Rig, str]]:
    tenant = str(two_tenants["alpha"])
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DMS_TENANT_ID", tenant)
    monkeypatch.setenv("DMS_ONTO_PG", "1")
    monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    reset_ontology_data_store()
    rig = make_rig(tmp_path, monkeypatch, make_minter(monkeypatch))
    store = ontology_data_store()
    assert isinstance(store, PostgresOntologyDataStore)
    rig.app.state.space_store = PostgresSpaceStore(migrated_db, tenant_id=tenant, role="steward")
    try:
        yield rig, tenant
    finally:
        reset_ontology_data_store()


def _space(rig: Rig, name: str) -> str:
    return rig.new_space(f"{name} {uuid.uuid4().hex[:8]}")


def _store() -> PostgresOntologyDataStore:
    store = ontology_data_store()
    assert isinstance(store, PostgresOntologyDataStore)
    return store


def test_pg_every_item_proposed_and_row_shape(pg_rig: tuple[Rig, str], migrated_db: str) -> None:
    rig, tenant = pg_rig
    space = _space(rig, "Onboard")
    items = sample_items()
    items[0] = (items[0][0], items[0][1] | {"status": "approved", "approved": True}, items[0][2])
    body = _store().store_onboard(items, created_by="", space_id=space, run=RUN)
    assert body["status"] == "proposed" and body["served"] is False
    assert body["space_id"] == space and body["version_id"]
    assert {i["status"] for i in body["items"]} == {"proposed"}
    assert {i["approved_by"] for i in body["items"]} == {None}
    assert {i["kind"] for i in body["items"]} == {
        "column_type",
        "value_map",
        "verified_query",
        "object",
    }
    with psycopg.connect(migrated_db) as conn:
        set_tenant_context(conn, tenant, role="admin")
        version = conn.execute(
            "SELECT space_id::text, source, provenance FROM dms.ontology_versions WHERE id = %s",
            (body["version_id"],),
        ).fetchone()
    assert version is not None
    assert version[0] == space and version[1] == ONBOARD_SOURCE
    assert version[2] == {"served": False, "run": RUN}
    again = _store().space_onboard_items(space)
    assert {i["id"] for i in again} == {i["id"] for i in body["items"]}
    assert all(i["provenance"]["run"] == RUN for i in again)


def test_pg_blank_space_and_unknown_kind_write_nothing(
    pg_rig: tuple[Rig, str], migrated_db: str
) -> None:
    rig, tenant = pg_rig
    space = _space(rig, "Onboard")
    store = _store()
    with pytest.raises(ValueError):
        store.store_onboard(sample_items(), created_by="", space_id="  ", run=RUN)
    bad = [*sample_items(), ("measure", {"name": "m"}, {})]
    with pytest.raises(ValueError):
        store.store_onboard(bad, created_by="", space_id=space, run=RUN)
    assert store.space_onboard_items(space) == []
    with psycopg.connect(migrated_db) as conn:
        set_tenant_context(conn, tenant, role="admin")
        ((n,),) = conn.execute(
            "SELECT COUNT(*) FROM dms.ontology_versions WHERE space_id = %s", (space,)
        ).fetchall()
    assert n == 0
    empty = store.store_onboard([], created_by="", space_id=space, run=RUN)
    assert empty["version_id"] is None and empty["items"] == []


def test_pg_space_scoping(pg_rig: tuple[Rig, str]) -> None:
    rig, _ = pg_rig
    a, b = _space(rig, "A"), _space(rig, "B")
    store = _store()
    store.store_onboard(sample_items(), created_by="", space_id=a, run=RUN)
    assert len(store.space_onboard_items(a)) == 4
    assert store.space_onboard_items(b) == []
    assert store.space_profile_items(a) == []
    assert store.space_onboard_items(a, "approved") == []
    assert len(store.space_onboard_items(a, "proposed")) == 4


def test_pg_approved_onboard_item_changes_no_read_and_revoke_works(pg_rig: tuple[Rig, str]) -> None:
    rig, _ = pg_rig
    space = _space(rig, "Onboard")
    store = _store()
    body = store.store_onboard(sample_items(), created_by="", space_id=space, run=RUN)
    for item in body["items"][:3]:
        r = _approve(rig, item["id"])
        assert r.status_code == 200, r.text
    assert {i["status"] for i in store.space_onboard_items(space)} == {"approved", "proposed"}
    assert store.approved_items() == []
    assert store.approved_items_of_space(space) == []
    assert store.approved_items_of_space(None) == []
    last = body["items"][3]
    assert _revoke(rig, last["id"]).status_code == 200
    assert [i["id"] for i in store.space_onboard_items(space, "revoked")] == [last["id"]]


def test_pg_route_source_param(pg_rig: tuple[Rig, str]) -> None:
    rig, _ = pg_rig
    a, b = _space(rig, "A"), _space(rig, "B")
    body = _store().store_onboard(sample_items(), created_by="", space_id=a, run=RUN)
    default = rig.client.get(f"/v1/ontology/data/spaces/{a}/items").json()
    assert default["items"] == [] and default["source"] == "profile"
    got = rig.client.get(f"/v1/ontology/data/spaces/{a}/items?source=fde_onboard").json()
    assert got["source"] == "fde_onboard" and got["persisted"] is True
    assert {i["id"] for i in got["items"]} == {i["id"] for i in body["items"]}
    other = rig.client.get(f"/v1/ontology/data/spaces/{b}/items?source=fde_onboard").json()
    assert other["items"] == []


# --- adversary round 1 (dms#541): re-run, tenant, diff, revoked read ------------------


def test_pg_rerun_keeps_held_items_and_a_revoked_one_stays_revoked(pg_rig: tuple[Rig, str]) -> None:
    rig, tenant = pg_rig
    space = _space(rig, "Onboard rerun")
    store = _store()
    first = store.store_onboard(
        sample_items(), created_by="fde", space_id=space, tenant_id=tenant, run=RUN
    )
    revoked = first["items"][0]["id"]
    _revoke(rig, revoked)
    second = store.store_onboard(
        sample_items(), created_by="fde", space_id=space, tenant_id=tenant, run=RUN
    )
    assert [i["id"] for i in second["items"]] == [i["id"] for i in first["items"]]
    assert second["version_id"] is None
    rows = store.space_onboard_items(space)
    assert len(rows) == len(sample_items())
    assert {r["id"]: r["status"] for r in rows}[revoked] == "revoked"


def test_pg_store_onboard_refuses_another_tenant(
    pg_rig: tuple[Rig, str], two_tenants: dict[str, Any]
) -> None:
    rig, _tenant = pg_rig
    space = _space(rig, "Onboard tenant")
    beta = str(two_tenants["beta"])
    with pytest.raises(ValueError, match="tenant_mismatch"):
        _store().store_onboard(
            sample_items(), created_by="fde", space_id=space, tenant_id=beta, run=RUN
        )
    assert _store().space_onboard_items(space) == []


def test_pg_diff_and_revoked_read_leave_onboard_rows_out(pg_rig: tuple[Rig, str]) -> None:
    rig, tenant = pg_rig
    space = _space(rig, "Onboard diff")
    store = _store()
    stored = store.store_onboard(
        sample_items(), created_by="fde", space_id=space, tenant_id=tenant, run=RUN
    )
    obj = next(i for i in stored["items"] if i["kind"] == "object")
    _approve(rig, obj["id"])
    proposal = store.store_proposal(
        {
            "status": "proposed",
            "terms": [{"name": "orders", "table": "orders", "key": ["order_id"]}],
        },
        created_by="ao",
        tenant_id=tenant,
    )
    diff = store.diff(proposal["version_id"])
    assert [i["payload"]["name"] for i in diff["proposed_only"]] == ["orders"]
    for item in stored["items"]:
        _revoke(rig, item["id"])
    assert not [i for i in store.revoked_items() if i["source"] == ONBOARD_SOURCE]


def _profile_body() -> dict[str, Any]:
    return {"keys": [{"table": "orders", "columns": ["order_id"], "evidence": {"rows": 3}}]}


def _proposal_body() -> dict[str, Any]:
    return {
        "status": "proposed",
        "terms": [{"name": "orders", "table": "orders", "key": ["order_id"]}],
    }


def _versions(migrated_db: str, tenant: str) -> int:
    with psycopg.connect(migrated_db) as conn:
        set_tenant_context(conn, tenant, role="steward")
        row = conn.execute("SELECT count(*) FROM dms.ontology_versions").fetchone()
    assert row is not None
    return int(row[0])


def test_pg_store_proposal_refuses_another_tenant(
    pg_rig: tuple[Rig, str], two_tenants: dict[str, Any], migrated_db: str
) -> None:
    _rig, alpha = pg_rig
    beta = str(two_tenants["beta"])
    before = {t: _versions(migrated_db, t) for t in (alpha, beta)}
    with pytest.raises(ValueError, match="tenant_mismatch"):
        _store().store_proposal(_proposal_body(), created_by="ao", tenant_id=beta)
    assert {t: _versions(migrated_db, t) for t in (alpha, beta)} == before
    ok = _store().store_proposal(_proposal_body(), created_by="ao", tenant_id=alpha)
    assert ok["version_id"] and len(ok["items"]) == 1
    bound = _store().store_proposal(_proposal_body(), created_by="ao")
    assert bound["version_id"] and bound["version_id"] != ok["version_id"]
    assert _versions(migrated_db, alpha) == before[alpha] + 2
    assert _versions(migrated_db, beta) == before[beta]


def test_pg_store_profile_refuses_another_tenant(
    pg_rig: tuple[Rig, str], two_tenants: dict[str, Any], migrated_db: str
) -> None:
    rig, alpha = pg_rig
    beta = str(two_tenants["beta"])
    space = _space(rig, "Profile tenant")
    before = {t: _versions(migrated_db, t) for t in (alpha, beta)}
    with pytest.raises(ValueError, match="tenant_mismatch"):
        _store().store_profile(_profile_body(), created_by="fde", space_id=space, tenant_id=beta)
    assert {t: _versions(migrated_db, t) for t in (alpha, beta)} == before
    assert _store().space_profile_items(space) == []
    ok = _store().store_profile(_profile_body(), created_by="fde", space_id=space, tenant_id=alpha)
    assert len(ok["items"]) == 1
    assert len(_store().space_profile_items(space)) == 1
    assert _versions(migrated_db, beta) == before[beta]
