"""ONTO-PROFILE-01 (dms#514) against the Postgres item store and Space store.

The CI-runnable twin (``tests/test_onto_profile_01.py``) runs every clause on
the in-memory store. This one turns ``DMS_ONTO_PG`` on over a migrated
database and asserts the store clauses on the same HTTP responses: the items
persist, belong to one Space, are not stored twice, and a second key for one
table is refused. Runs wherever the control-plane suite runs (R-0002).
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from dms_core.control_plane.onto_data import (
    PostgresOntologyDataStore,
    ProfileKeyConflict,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_core.control_plane.pg_spaces import PostgresSpaceStore
from dms_core.control_plane.session import set_tenant_context
from dms_executor.ontology import Ontology

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_onto_derive_01 import make_minter  # noqa: E402
from test_onto_profile_01 import (  # noqa: E402
    Rig,
    _approve,
    _asks,
    _csv,
    _item,
    _keys,
    _links,
    _revoke,
    _space_items,
    make_rig,
    retail_files,
)


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
    assert isinstance(ontology_data_store(), PostgresOntologyDataStore)
    # ``create_app`` without lifespan binds the memory catalog. The item store's
    # version row references ``dms.spaces``, so the Space has to exist there.
    rig.app.state.space_store = PostgresSpaceStore(migrated_db, tenant_id=tenant, role="steward")
    try:
        yield rig, tenant
    finally:
        reset_ontology_data_store()


def _rows(url: str, tenant: str, sql: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as conn:
        set_tenant_context(conn, tenant, role="admin")
        return conn.execute(sql).fetchall()


def _space(rig: Rig, name: str) -> str:
    return rig.new_space(f"{name} {uuid.uuid4().hex[:8]}")


def test_pg_profile_persists_and_belongs_to_one_space(
    pg_rig: tuple[Rig, str], migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, tenant = pg_rig
    space_a = _space(rig, "Retail files")
    space_b = _space(rig, "Other files")
    t = rig.ingest(space_a, retail_files())
    rig.ingest(space_b, {"tags.csv": _csv(["tag", "colour"], [["t1", "red"], ["t2", "red"]])})

    body = rig.profile(space_a)
    assert body["status"] == "proposed" and body["served"] is False
    assert body["space_id"] == space_a and body["version_id"]
    assert body["persisted"] is True and "hint" not in body
    assert len(body["items"]) == 8
    assert _keys(body, t["customers.csv"]) == [["customer_id"], ["name"]]
    assert {i["source"] for i in body["items"]} == {"profile"}
    assert {i["status"] for i in body["items"]} == {"proposed"}

    # The version row carries the Space; a later connection reads the same items.
    versions = _rows(
        migrated_db,
        tenant,
        "SELECT space_id::text, source FROM dms.ontology_versions "
        f"WHERE id = '{body['version_id']}'",
    )
    assert versions == [(space_a, "profile")]
    reader = PostgresOntologyDataStore(migrated_db, tenant_id=tenant)
    assert {i["id"] for i in reader.space_profile_items(space_a)} == {
        i["id"] for i in body["items"]
    }

    key = _item(body, t["orders.csv"], ["order_id"])
    (link,) = _links(body)
    assert _approve(rig, key["id"]).status_code == 200
    assert _approve(rig, link["id"]).status_code == 200
    assert {i["id"] for i in _space_items(rig, space_a, "approved")} == {key["id"], link["id"]}
    assert _space_items(rig, space_b) == []
    assert reader.space_profile_items(space_b) == []
    # Tenant-wide read, and so the data ontology, never contains a profile item.
    assert reader.approved_items() == []
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    onto = Ontology.from_store()
    assert onto.objects == {} and onto.links == {}

    # No cell value in a stored row: every id in the fixture starts with a letter
    # and four digits or more; the stored JSON holds names, types and counts.
    stored = _rows(
        migrated_db,
        tenant,
        "SELECT payload::text, provenance::text FROM dms.ontology_items WHERE source = 'profile'",
    )
    text = json.dumps(stored)
    for value in ("C001", "O0001", "Customer 001", "Product 01", "S0001", "Ipoh"):
        assert value not in text, value


def test_pg_profile_twice_stores_no_second_copy(pg_rig: tuple[Rig, str], migrated_db: str) -> None:
    rig, tenant = pg_rig
    space_id = _space(rig, "Retail files")
    t = rig.ingest(space_id, retail_files())
    first = rig.profile(space_id)
    gone = _item(first, t["customers.csv"], ["name"])
    kept = _item(first, t["customers.csv"], ["customer_id"])
    assert _revoke(rig, gone["id"]).status_code == 200
    assert _approve(rig, kept["id"]).status_code == 200

    second = rig.profile(space_id)
    by_id = {i["id"]: i for i in second["items"]}
    assert set(by_id) == {i["id"] for i in first["items"]}
    assert second["version_id"] == first["version_id"] and second["not_remeasured"] == []
    assert by_id[gone["id"]]["status"] == "revoked"
    assert by_id[gone["id"]]["provenance"]["revoked_by"]
    assert by_id[kept["id"]]["status"] == "approved" and by_id[kept["id"]]["approval_ref"]
    assert by_id[kept["id"]]["provenance"]["evidence"]["row_count"] == 60

    scoped = f"WHERE v.space_id = '{space_id}'"
    ((items,),) = _rows(
        migrated_db,
        tenant,
        "SELECT COUNT(*) FROM dms.ontology_items i "
        f"JOIN dms.ontology_versions v ON v.id = i.version_id {scoped}",
    )
    ((versions,),) = _rows(
        migrated_db, tenant, f"SELECT COUNT(*) FROM dms.ontology_versions v {scoped}"
    )
    assert (items, versions) == (8, 1)


def test_pg_second_key_for_one_table_is_refused_and_changes_nothing(
    pg_rig: tuple[Rig, str], migrated_db: str
) -> None:
    rig, tenant = pg_rig
    space_id = _space(rig, "Retail files")
    t = rig.ingest(space_id, retail_files())
    body = rig.profile(space_id)
    first = _item(body, t["customers.csv"], ["customer_id"])
    second = _item(body, t["customers.csv"], ["name"])

    store = PostgresOntologyDataStore(migrated_db, tenant_id=tenant)
    with pytest.raises(ProfileKeyConflict):
        store.approve_version(body["version_id"], str(uuid.uuid4()))
    assert {i["status"] for i in _space_items(rig, space_id)} == {"proposed"}

    ok = _approve(rig, first["id"])
    assert ok.status_code == 200, ok.text
    refused = _approve(rig, second["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == {
        "code": "key_already_approved",
        "relation": t["customers.csv"],
        "approved_item_id": first["id"],
    }
    after = {i["id"]: i for i in _space_items(rig, space_id)}
    assert after[second["id"]]["status"] == "proposed"
    assert after[second["id"]]["approved_by"] is None
    assert after[first["id"]]["approval_ref"] == ok.json()["approval_ref"]
    assert [i["id"] for i in _space_items(rig, space_id, "approved")] == [first["id"]]
    assert _revoke(rig, first["id"]).status_code == 200
    assert _approve(rig, second["id"]).status_code == 200


def test_pg_approved_profile_items_leave_the_ask_envelope_unchanged(
    pg_rig: tuple[Rig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _tenant = pg_rig
    space_id = _space(rig, "Retail files")
    t = rig.ingest(space_id, retail_files())
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    before = _asks(rig, space_id, t)
    assert before["count"]["rows"] == [{"n": 300}]
    assert before["proposed_link_join"]["badge"] == "ABSTAIN"
    body = rig.profile(space_id)
    approved = [i["id"] for i in body["items"] if _approve(rig, i["id"]).status_code == 200]
    assert len(approved) == 5
    assert _asks(rig, space_id, t) == before
