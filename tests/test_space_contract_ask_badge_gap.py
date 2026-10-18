"""KNOWN GAP (P-DMS-57, carve-out of P-DMS-48 / spec step 9): the contract ask's own badge.

A SQL-source Space answers a certified-tier badge only through a steward-confirmed measure
(DR-0006). The contract-ask tail of ``Executor._live_ask`` does not enforce that: it maps the
engine's badge straight onto the envelope, so a stubbed engine that says ``governed_metric`` or
``certified`` reaches the customer as ``L1_GOVERNED_METRIC`` or ``L0_CERTIFIED`` over a Space with
no confirmed measure and no ``measure_basis``. The fix is the egress finalizer of spec step 5.7,
which the founder gated with steps 8, 9, 9b and 11. It is not built, and ad hoc generated SQL keeps
today's ``L2_VALIDATED`` path.

These tests do NOT bless the badge. They pin today's behaviour exactly so that:

* the leak stays narrow and visible: it is the badge only, the rendered text and the rows are the
  engine's own and ``measure_basis`` is absent, so nothing claims a measure that was not confirmed;
* lifting the carve-out cannot land silently. The moment the finalizer exists the two L1/L0 cases
  turn red. Invert them then: assert the badge is not ``L0_CERTIFIED``/``L1_GOVERNED_METRIC``
  without a valid ``measure_basis`` (and take the rung the founder decides: clamp to
  ``L2_VALIDATED`` as the adversary proposed, or abstain ``uncertified_l1`` as spec 5.7 reads),
  then strike P-DMS-57 from PARKING_LOT.md.

Everything is asserted at the customer envelope (``POST /v1/chat/ask`` via
``assert_envelope_valid``), on the rendered text and the returned rows, not on the SQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import set_ontology_store
from test_onto_derive_01 import _space, make_minter

ENGINE_ANSWER = "There are 4 schools."
SQL = "SELECT COUNT(*) AS n FROM bronze.public_schools"
QUESTION = "how many schools exist overall"


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


def _ask_with_engine_badge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
    engine_badge: str,
) -> dict[str, object]:
    """The derived Space, a stubbed contract ask, one POST /v1/chat/ask. Precondition checked."""
    rig = _space(tmp_path, monkeypatch, minter)
    # The precondition that makes the badge a leak: nothing on this Space is confirmed.
    assert store.measures_for_space(UUID(rig.space_id), states=["confirmed"]) == []

    rig.cortex.ask = lambda req: AskResponse(  # type: ignore[method-assign]
        answer=ENGINE_ANSWER,
        badge=engine_badge,
        audit_id="aud_gap",
        route="query_skill",
        sql_used=SQL,
        rows=[{"n": 4}],
        drillthrough_token="dt_gap",
    )
    env: dict[str, object] = rig.ask(QUESTION, {})
    assert_envelope_valid(env)
    return env


@pytest.mark.parametrize(
    ("engine_badge", "leaked_badge"),
    [("governed_metric", "L1_GOVERNED_METRIC"), ("certified", "L0_CERTIFIED")],
)
def test_known_gap_a_contract_ask_certified_badge_reaches_a_space_with_no_confirmed_measure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
    engine_badge: str,
    leaked_badge: str,
) -> None:
    """TRIPWIRE: red the day the egress finalizer lands. Invert it then (see the module doc)."""
    env = _ask_with_engine_badge(tmp_path, monkeypatch, minter, store, engine_badge)

    # The gap, exactly: the engine's certified-tier badge is trusted on a Space.
    assert env["badge"] == leaked_badge
    # Narrowness: it is the engine's own answer, unchanged, and carries no measure claim.
    assert env["abstained"] is False
    assert ENGINE_ANSWER in str(env["text"])
    assert env["rows"] == [{"n": 4}]
    assert "measure_basis" not in env
    assert not any(str(a).startswith("measure confirmed:") for a in env["assumptions"])  # type: ignore[attr-defined]


def test_a_contract_ask_query_badge_stays_l2_validated_on_a_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """The control for the gap above: a non-certified engine badge is not promoted."""
    env = _ask_with_engine_badge(tmp_path, monkeypatch, minter, store, "query_skill")

    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert ENGINE_ANSWER in str(env["text"])
    assert env["rows"] == [{"n": 4}]
    assert "measure_basis" not in env
