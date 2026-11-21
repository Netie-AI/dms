"""FDE-ONBOARD-01 (dms#541) gated import route for onboarding proposals.

``POST /v1/ontology/data/spaces/{space_id}/onboard`` asserted on the HTTP
response a steward receives and on what the item store holds afterwards. The
route only proposes: nothing here approves.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cortex_client.gate import ComplianceDecision
from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
from dms_executor.manifest import ManifestMinter
from test_fde_onboard_store_01 import RUN, sample_items
from test_onto_derive_01 import make_minter
from test_onto_profile_01 import Rig, make_rig

ACTION = "ontology.onboard_import"


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


def _body(items: list[tuple[str, dict[str, Any], dict[str, Any]]] | None = None) -> dict[str, Any]:
    return {
        "items": [
            {"kind": k, "payload": p, "evidence": e}
            for k, p, e in (sample_items() if items is None else items)
        ],
        "run": RUN,
    }


def _post(rig: Rig, space: str, body: dict[str, Any]) -> Any:
    return rig.client.post(f"/v1/ontology/data/spaces/{space}/onboard", json=body)


def test_gate_denies_is_403_and_nothing_is_stored(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_api.routes.ontology as onto_routes

    space = rig.new_space("Onboard deny")

    def _deny(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=False, reason="policy_denied", action=action)

    monkeypatch.setattr(onto_routes, "compliance_gate", _deny)
    res = _post(rig, space, _body())
    assert res.status_code == 403, res.text
    assert ontology_data_store().space_onboard_items(space) == []


def test_gate_allows_every_item_is_proposed_never_approved(rig: Rig) -> None:
    space = rig.new_space("Onboard allow")
    items = sample_items()
    items[0] = (items[0][0], items[0][1] | {"status": "approved", "approved_by": "me"}, items[0][2])
    res = _post(rig, space, _body(items))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "proposed" and body["served"] is False
    assert body["persisted"] is False
    assert body["space_id"] == space and len(body["items"]) == 4
    for item in body["items"]:
        assert item["status"] == "proposed"
        assert item["approved_by"] is None
    held = ontology_data_store().space_onboard_items(space)
    assert {i["status"] for i in held} == {"proposed"}
    assert {i["approved_by"] for i in held} == {None}
    assert len(held) == 4


def test_non_steward_is_403_and_the_gate_is_not_called(rig: Rig) -> None:
    from dms_api.settings import Settings

    space = rig.new_space("Onboard role")
    ontology_data_store().deny_steward(Settings(_env_file=None).dms_actor_user_id)  # type: ignore[call-arg]
    res = _post(rig, space, _body())
    assert res.status_code == 403, res.text
    assert res.json()["detail"] == "steward_grant_required"
    assert ACTION not in rig.gate_actions
    assert ontology_data_store().space_onboard_items(space) == []


def test_unknown_space_is_404(rig: Rig) -> None:
    res = _post(rig, "00000000-0000-4000-8000-000000000999", _body())
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "space_not_found"


def test_unknown_kind_is_422_and_writes_nothing(rig: Rig) -> None:
    space = rig.new_space("Onboard kind")
    items = [*sample_items(), ("measure", {"name": "m"}, {})]
    res = _post(rig, space, _body(items))
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "unknown_onboard_kind"
    assert ontology_data_store().space_onboard_items(space) == []


def test_more_than_500_items_is_refused(rig: Rig) -> None:
    space = rig.new_space("Onboard cap")
    one = sample_items()[0]
    res = _post(rig, space, _body([one] * 501))
    assert res.status_code == 422, res.text
    assert ontology_data_store().space_onboard_items(space) == []


def test_gate_is_called_before_the_store_is_touched(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_api.routes.ontology as onto_routes

    space = rig.new_space("Onboard order")
    store = ontology_data_store()
    calls: list[str] = []
    real_gate = onto_routes.compliance_gate
    real_store = type(store).store_onboard
    seen: dict[str, Any] = {}

    def _gate(**kwargs: Any) -> ComplianceDecision:
        calls.append("gate")
        seen.update(kwargs)
        return real_gate(**kwargs)

    def _store(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append("store")
        return real_store(self, *args, **kwargs)

    monkeypatch.setattr(onto_routes, "compliance_gate", _gate)
    monkeypatch.setattr(type(store), "store_onboard", _store)
    res = _post(rig, space, _body())
    assert res.status_code == 200, res.text
    assert calls == ["gate", "store"]
    assert seen["action"] == ACTION
    assert seen["metadata"] == {"task_id": ACTION, "space_id": space, "item_count": 4}
    assert rig.gate_actions == [ACTION]


def test_second_import_keeps_ids_and_statuses(rig: Rig) -> None:
    space = rig.new_space("Onboard rerun")
    first = _post(rig, space, _body())
    assert first.status_code == 200, first.text
    ids = {i["name"] if "name" in i else i["id"]: i["id"] for i in first.json()["items"]}
    held = ontology_data_store().space_onboard_items(space)
    victim = held[0]["id"]
    assert ontology_data_store().revoke(victim, actor="steward") is not None
    second = _post(rig, space, _body())
    assert second.status_code == 200, second.text
    again = ontology_data_store().space_onboard_items(space)
    assert len(again) == 4
    assert {i["id"] for i in again} == {i["id"] for i in held}
    assert {i["id"]: i["status"] for i in again}[victim] == "revoked"
    assert {i["id"] for i in second.json()["items"]} == set(ids.values())
