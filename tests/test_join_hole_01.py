# ruff: noqa: E501
"""JOIN-HOLE-01 (dms#527): a CTE self-join takes the same join gate.

Must-fail on the parent early return. Each shape in ``JOIN_HOLE_SHAPES`` names
one base table and reads it twice. The parent served ``wrong`` (L2_VALIDATED,
the figure in rows, values and text). The true one-table figure is ``true``.
After the fix every door abstains ``unverified_join`` with no row and no figure.

Doors: generate (default flags), extract loop (``DMS_CLOOP_B=1``), contract
``sql_used``. No live model: the generate leg returns the fixed SQL.

A file Space with no ontology does not call this gate (that is #522, not on
this main). Held SQL confirmed with Yes does not call it either (that is
#524). Those two doors are not pinned here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.envelope import assert_envelope_valid
from dms_executor.space_ontology import set_ontology_store

# No named filter. VERIFIED_Q says "North", and a one-table read that does not
# filter North abstains unhonored_qualifier before it can answer.
_Q = "How many schools are there?"


def _open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[od._Space, Any]:
    minter = od.make_minter(monkeypatch)
    prev = set_ontology_store(OntologyStore())
    return od._space(tmp_path, monkeypatch, minter), prev


def _state_figures(rig: od._Space, shape: dict[str, Any]) -> None:
    """The attack really returns ``wrong``; the one-table read returns ``true``."""
    attack = od._oracle(rig.lake, shape["sql"])
    truth = od._oracle(rig.lake, shape["true_sql"])
    assert attack == [{"total": shape["wrong"]}], attack
    assert truth == [{"total": shape["true"]}], truth
    assert shape["wrong"] != shape["true"]


def _assert_no_figure(env: dict[str, Any], shape: dict[str, Any], gap: str) -> None:
    assert_envelope_valid(env)
    text = od._assert_abstain(env, gap)
    assert str(shape["wrong"]) not in text, text
    assert "total=" not in text
    assert shape["sql"] not in (env.get("sql_used") or "")


def _assert_serves(env: dict[str, Any], rig: od._Space, sql: str) -> None:
    assert_envelope_valid(env)
    oracle = od._oracle(rig.lake, sql)
    assert oracle, sql
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert od._multiset(env["rows"]) == od._multiset(oracle)
    shown = str(next(iter(oracle[0].values())))
    assert shown in str(env.get("text") or ""), env.get("text")


def test_cte_self_join_abstains_on_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, prev = _open(tmp_path, monkeypatch)
    try:
        for name, shape in od.JOIN_HOLE_SHAPES.items():
            _state_figures(rig, shape)
            env = rig.ask(_Q, {"query_sql": shape["sql"], "plan_source": "ontology_plan"})
            _assert_no_figure(env, shape, "unverified_join")
            assert shape["sql"] not in rig.cortex.executed, name
        for name, shape in od.JOIN_HOLE_STOPPED.items():
            env = rig.ask(_Q, {"query_sql": shape["sql"], "plan_source": "ontology_plan"})
            text = od._assert_abstain(env, "unverified_join")
            assert "total=" not in text, (name, text)
            assert shape["sql"] not in rig.cortex.executed, name
    finally:
        set_ontology_store(prev)


def test_cte_self_join_abstains_on_the_extract_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig, prev = _open(tmp_path, monkeypatch)
    try:
        for name, shape in {**od.JOIN_HOLE_SHAPES, **od.JOIN_HOLE_STOPPED}.items():
            env = rig.ask(_Q, {"query_sql": shape["sql"], "plan_source": "ontology_plan"})
            text = od._assert_abstain(env, "loop_exhausted:checker:unverified_join")
            wrong = shape.get("wrong")
            if wrong is not None:
                assert str(wrong) not in text, (name, text)
            assert "total=" not in text, (name, text)
            assert shape["sql"] not in rig.cortex.executed, name
    finally:
        set_ontology_store(prev)


def test_cte_self_join_abstains_on_contract_sql_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, prev = _open(tmp_path, monkeypatch)
    try:
        held: dict[str, Any] = {}

        def _ask(req: Any) -> AskResponse:
            rig.cortex.asks.append(req)
            wrong = held.get("wrong", 0)
            return AskResponse(
                answer=f"The total is {wrong}.",
                abstained=False,
                badge="generated",
                route="generated",
                sql_used=held["sql"],
                rows=[{"total": wrong}],
            )

        monkeypatch.setattr(rig.cortex, "ask", _ask)
        for name, shape in od.JOIN_HOLE_SHAPES.items():
            _state_figures(rig, shape)
            held["sql"] = shape["sql"]
            held["wrong"] = shape["wrong"]
            env = rig.ask(_Q, {})
            assert rig.cortex.asks, name
            _assert_no_figure(env, shape, "unverified_join")
            assert "The total is" not in str(env.get("text") or ""), name
        for name, shape in od.JOIN_HOLE_STOPPED.items():
            held["sql"] = shape["sql"]
            held["wrong"] = 999999
            env = rig.ask(_Q, {})
            text = od._assert_abstain(env, "unverified_join")
            assert "999999" not in text, (name, text)
            assert "The total is" not in text, name
    finally:
        set_ontology_store(prev)


def test_one_read_of_one_table_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same outcome as the parent, on each door."""
    rig, prev = _open(tmp_path, monkeypatch)
    try:
        for name, sql in od.JOIN_HOLE_STILL.items():
            env = rig.ask(_Q, {"query_sql": sql, "plan_source": "ontology_plan"})
            _assert_serves(env, rig, sql)
            assert sql in rig.cortex.executed, name
    finally:
        set_ontology_store(prev)

    contract = tmp_path / "contract"
    contract.mkdir()
    rig, prev = _open(contract, monkeypatch)
    try:
        held: dict[str, str] = {"sql": ""}

        def _ask(req: Any) -> AskResponse:
            rig.cortex.asks.append(req)
            rows = od._oracle(rig.lake, held["sql"])
            shown = str(next(iter(rows[0].values()))) if rows else ""
            return AskResponse(
                answer=f"Found {shown}.",
                abstained=False,
                badge="generated",
                route="generated",
                sql_used=held["sql"],
                rows=rows,
            )

        monkeypatch.setattr(rig.cortex, "ask", _ask)
        for name, sql in od.JOIN_HOLE_STILL.items():
            held["sql"] = sql
            env = rig.ask(_Q, {})
            _assert_serves(env, rig, sql)
            assert sql in str(env.get("sql_used") or ""), name
    finally:
        set_ontology_store(prev)

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    loop = tmp_path / "loop"
    loop.mkdir()
    rig, prev = _open(loop, monkeypatch)
    try:
        for name, sql in od.JOIN_HOLE_STILL.items():
            env = rig.ask(_Q, {"query_sql": sql, "plan_source": "ontology_plan"})
            _assert_serves(env, rig, sql)
            assert sql in rig.cortex.executed, name
    finally:
        set_ontology_store(prev)


def test_values_and_unnest_joins_still_abstain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controls. The parent already refused these. They must not start serving."""
    rig, prev = _open(tmp_path, monkeypatch)
    try:
        for sql in (
            f"SELECT SUM(CAST(o.students AS INTEGER)) AS total FROM {od.ENROLL} o, (VALUES (1), (2)) v(n)",
            f"SELECT SUM(CAST(o.students AS INTEGER)) AS total FROM {od.ENROLL} o, UNNEST([1, 2]) u(n)",
            (
                f"SELECT SUM(CAST(a.school_id AS INTEGER)) AS total FROM {od.SCHOOLS} a "
                f"JOIN {od.SCHOOLS} b ON a.district_id = b.district_id"
            ),
        ):
            env = rig.ask(_Q, {"query_sql": sql, "plan_source": "ontology_plan"})
            od._assert_abstain(env, "unverified_join")
            assert sql not in rig.cortex.executed
    finally:
        set_ontology_store(prev)
