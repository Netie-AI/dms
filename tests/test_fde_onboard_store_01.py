"""FDE-ONBOARD-01 (dms#541) item store: onboard items are proposals, never served.

Store clauses on the in-memory store, plus the steward read route through
``TestClient``. ``tests/control_plane/test_fde_onboard_store_01_pg.py`` runs the
same clauses against Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.onto_data import (
    KINDS,
    ONBOARD_KINDS,
    ONBOARD_SOURCE,
    ontology_data_store,
    reset_ontology_data_store,
    store_onboard,
)
from dms_executor.manifest import ManifestMinter
from test_onto_derive_01 import make_minter
from test_onto_profile_01 import Rig, _approve, _revoke, make_rig

RUN = {"run_id": "run-541", "stage": "type_and_clean"}


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Rig:
    return make_rig(tmp_path, monkeypatch, minter)


def sample_items() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    return [
        (
            "column_type",
            {
                "name": "orders.amount",
                "table": "orders",
                "column": "amount",
                "type": "DECIMAL(38,2)",
            },
            {"non_null": 300, "scale": 2},
        ),
        (
            "value_map",
            {
                "name": "orders.status",
                "table": "orders",
                "column": "status",
                "groups": [["a", "A"]],
            },
            {"fold": "case_space"},
        ),
        (
            "verified_query",
            {"name": "q1", "question": "how many orders", "sql": "SELECT COUNT(*) FROM orders"},
            {"rows": 1},
        ),
        ("object", {"name": "orders", "relation": "orders", "key": ["order_id"]}, {"rows": 300}),
    ]


def test_kinds_extend_without_reordering() -> None:
    assert KINDS[:5] == ("object", "link", "measure", "grain", "pii")
    assert {"column_type", "value_map", "verified_query"} <= set(KINDS)
    assert ONBOARD_KINDS == ("object", "link", "column_type", "value_map", "verified_query")


def test_every_item_is_proposed_even_when_the_payload_says_approved(rig: Rig) -> None:
    space = rig.new_space("Onboard")
    items = sample_items()
    hostile = items[0][1] | {"status": "approved", "approved": True, "approved_by": "me"}
    items[0] = (items[0][0], hostile, {**items[0][2], "status": "approved"})
    body = store_onboard(items, created_by="fde", space_id=space, run=RUN)
    assert body["status"] == "proposed" and body["served"] is False
    assert body["space_id"] == space and body["version_id"]
    assert len(body["items"]) == 4
    for item in body["items"]:
        assert item["status"] == "proposed"
        assert item["approved_by"] is None
        assert item["source"] == ONBOARD_SOURCE
        assert item["provenance"]["run"] == RUN
        assert "evidence" in item["provenance"]
    stored = ontology_data_store().space_onboard_items(space)
    assert {i["status"] for i in stored} == {"proposed"}
    assert {i["approved_by"] for i in stored} == {None}


def test_empty_items_make_no_version(rig: Rig) -> None:
    space = rig.new_space("Onboard")
    body = store_onboard([], created_by="fde", space_id=space, run=RUN)
    assert body["version_id"] is None and body["items"] == []


def test_blank_space_is_refused() -> None:
    for blank in ("", "   "):
        with pytest.raises(ValueError):
            store_onboard(sample_items(), created_by="fde", space_id=blank, run=RUN)


def test_unknown_kind_raises_and_writes_nothing(rig: Rig) -> None:
    space = rig.new_space("Onboard")
    bad = [*sample_items(), ("measure", {"name": "m"}, {})]
    with pytest.raises(ValueError):
        store_onboard(bad, created_by="fde", space_id=space, run=RUN)
    store = ontology_data_store()
    assert store.space_onboard_items(space) == []
    assert store.diff()["proposed_only"] == []


def test_one_space_never_sees_another_nor_the_profile_read(rig: Rig) -> None:
    a = rig.new_space("A")
    b = rig.new_space("B")
    store_onboard(sample_items(), created_by="fde", space_id=a, run=RUN)
    store = ontology_data_store()
    assert len(store.space_onboard_items(a)) == 4
    assert store.space_onboard_items(b) == []
    assert store.space_profile_items(a) == []
    assert store.space_onboard_items(a, "approved") == []
    assert len(store.space_onboard_items(a, "proposed")) == 4


def test_approved_onboard_item_changes_no_read(rig: Rig) -> None:
    space = rig.new_space("Onboard")
    body = store_onboard(sample_items(), created_by="fde", space_id=space, run=RUN)
    store = ontology_data_store()
    for item in body["items"]:
        r = _approve(rig, item["id"])
        assert r.status_code == 200, r.text
    assert {i["status"] for i in store.space_onboard_items(space)} == {"approved"}
    assert store.approved_items() == []
    assert store.approved_items_of_space(space) == []
    assert store.approved_items_of_space(None) == []


def test_route_reads_onboard_and_default_stays_profile(rig: Rig) -> None:
    a = rig.new_space("A")
    b = rig.new_space("B")
    body = store_onboard(sample_items(), created_by="fde", space_id=a, run=RUN)
    default = rig.client.get(f"/v1/ontology/data/spaces/{a}/items")
    assert default.status_code == 200, default.text
    assert default.json()["items"] == []
    assert default.json()["source"] == "profile"
    got = rig.client.get(f"/v1/ontology/data/spaces/{a}/items?source=fde_onboard")
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["source"] == "fde_onboard" and data["served"] is False
    assert {i["id"] for i in data["items"]} == {i["id"] for i in body["items"]}
    other = rig.client.get(f"/v1/ontology/data/spaces/{b}/items?source=fde_onboard")
    assert other.json()["items"] == []
    only = rig.client.get(f"/v1/ontology/data/spaces/{a}/items?source=fde_onboard&status=approved")
    assert only.json()["items"] == []
    bad = rig.client.get(f"/v1/ontology/data/spaces/{a}/items?source=other")
    assert bad.status_code == 422


def test_revoke_works_on_onboard_items(rig: Rig) -> None:
    space = rig.new_space("Onboard")
    body = store_onboard(sample_items(), created_by="fde", space_id=space, run=RUN)
    first = body["items"][0]
    r = _revoke(rig, first["id"])
    assert r.status_code == 200, r.text
    got = rig.client.get(
        f"/v1/ontology/data/spaces/{space}/items?source=fde_onboard&status=revoked"
    )
    assert [i["id"] for i in got.json()["items"]] == [first["id"]]
    assert ontology_data_store().approved_items() == []
