# ruff: noqa: F811 - the imported fixtures are used by name
"""VQ-03 (b): the data flag governs the verified-query door as it governs the ask.

Clause 11 of test_onto_profile_02: under ``DMS_ONTO_FROM_DATA=1`` the approved
profile link is not read; the Space's item ontology (``onto_scope.ask_ontology``,
the one shared resolver) governs. At 7ffbe45 the verified-query door read the
profile link and served L0 under the flag while the ask doors abstained.
"""

from __future__ import annotations

import pytest
import test_onto_derive_01 as od
import test_onto_profile_02 as p2
from dms_executor.envelope import assert_envelope_valid
from test_onto_profile_01 import Rig
from test_onto_profile_02 import _reset_store, minter, rig, store  # noqa: F401


@pytest.mark.parametrize("flag", ["0", "1"])
def test_the_data_flag_governs_the_verified_query_door_as_it_governs_the_ask(
    flag: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    if flag == "1":
        monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    space = p2.Space(rig)
    space.approve_the_link()
    question = f"VQ-03 profile join {flag}?"
    reg = rig.client.post(
        "/v1/studio/verified-queries",
        json={"space_id": space.id, "question": question, "sql": space.join_sql},
    )
    r = rig.client.post(
        "/v1/chat/ask", json={"question": question, "space_id": space.id, "session_id": "s"}
    )
    env = r.json()
    assert_envelope_valid(env)
    if flag == "1":
        assert reg.status_code == 400, reg.text
        assert str(reg.json()["detail"]).startswith("unverified_join:"), reg.text
        assert env["badge"] != "L0_CERTIFIED", env
        assert rig.cortex.executed == []
    else:
        assert reg.status_code == 200, reg.text
        assert env["badge"] == "L0_CERTIFIED", (env["badge"], env.get("text"))
        truth = p2.oracle(rig.lake, space.join_sql)
        assert truth
        assert od._multiset(env["rows"]) == od._multiset(truth)
