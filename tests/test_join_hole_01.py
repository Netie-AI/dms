# ruff: noqa: E501 - the SQL shapes read best one per line
"""JOIN-HOLE-01 (dms#527): one table multiplied through a second row source.

Rule 16's join allowlist counted base tables only. One table read twice
through a CTE name, or joined to a row source that is not a base table (a
recursive CTE, a constant derived table, a CTE that reads no table, UNNEST in
a projection), counted as one table and skipped every join check. The
fanned-out total was served ``L2_VALIDATED``.

Every case posts ``POST /v1/chat/ask`` in the Space with a verified ontology
(``test_onto_derive_01._space``) and asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows). The Cortex fake runs
the SQL on the lake, so the wrong figure each shape would show is real, and
each test states it beside the true one. Three doors: the first generate, the
extract loop, and the contract ask's ``sql_used``.

Not here, by the issue: the same shapes in a file Space with no ontology
(needs PR #522's gate) and held SQL confirmed with Yes (needs #524).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import (
    load_space_ontology,
    relation_columns,
    unverified_join_reason,
)
from test_onto_derive_01 import minter, store  # noqa: F401 - fixtures

E = od.ENROLL
Q = "What is the total?"
DOORS = ("generate", "loop", "contract")
TRUE_SQL = {
    107: f"SELECT SUM(CAST(students AS INTEGER)) AS total FROM {E}",
    10: f"SELECT SUM(CAST(school_id AS INTEGER)) AS total FROM {od.SCHOOLS}",
}

#: One-table statements that answered on the parent and abstain now (issue
#: clause 6): name -> (question, sql). None of them shows a wrong figure; the
#: rule cannot tell them from the shapes above, so they refuse by name.
NEWLY_ABSTAIN: dict[str, tuple[str, str]] = {
    "cte_filtered_against_its_own_average": (
        "Which schools have more students than average?",
        f"WITH c AS (SELECT school_id, SUM(CAST(students AS INTEGER)) AS n FROM {E} GROUP BY school_id) SELECT school_id, n FROM c WHERE n > (SELECT AVG(n) FROM c) ORDER BY school_id",
    ),
    "cte_divided_by_its_own_total": (
        "Show each school's share of students",
        f"WITH c AS (SELECT school_id, SUM(CAST(students AS INTEGER)) AS n FROM {E} GROUP BY school_id) SELECT school_id, n * 100 / (SELECT SUM(n) FROM c) AS pct FROM c ORDER BY school_id",
    ),
    "constant_union_branch": (Q, f"{od._E_TOTAL} UNION ALL SELECT 0"),
    "cte_unioned_with_itself": (
        "List the students per enrollment",
        f"WITH c AS (SELECT enrollment_id, students FROM {E}) SELECT * FROM c UNION ALL SELECT * FROM c",
    ),
    "cte_intersected_with_itself": (
        "List the students per enrollment",
        f"WITH c AS (SELECT enrollment_id, students FROM {E}) SELECT * FROM c INTERSECT SELECT * FROM c",
    ),
    "cte_except_itself_filtered": (
        "List the students per enrollment",
        f"WITH c AS (SELECT enrollment_id, students FROM {E}) SELECT * FROM c EXCEPT SELECT * FROM c WHERE enrollment_id = '100'",
    ),
    "one_row_constant_cross_join": (
        Q,
        f"SELECT SUM(CAST(o.students AS INTEGER)) AS total FROM {E} o CROSS JOIN (SELECT 1 AS n) v",
    ),
    "unnest_in_a_listing": (
        "List the enrollments",
        f"SELECT enrollment_id, UNNEST([1, 2]) AS u FROM {E} ORDER BY 1, 2",
    ),
    "recursive_keyword_without_recursion": (
        Q,
        f"WITH RECURSIVE c AS (SELECT * FROM {E}) SELECT SUM(CAST(students AS INTEGER)) AS total FROM c",
    ),
    "in_values_list": (
        Q,
        f"SELECT SUM(CAST(students AS INTEGER)) AS total FROM {E} WHERE school_id IN (VALUES ('1'), ('2'))",
    ),
}


def _ask(
    door: str,
    sql: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    question: str = Q,
) -> tuple[od._Space, dict[str, Any]]:
    """Carry ``sql`` to the envelope through one door."""
    if door == "loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = od._space(tmp_path, monkeypatch, minter)
    if door != "contract":
        return rig, rig.ask(question, {"query_sql": sql, "plan_source": "ontology_plan"})
    rows = od._oracle(rig.lake, sql)
    figure = next(iter(rows[0].values())) if rows else None

    def _contract(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer=f"The total is {figure}.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=sql,
            rows=rows,
        )

    monkeypatch.setattr(rig.cortex, "ask", _contract)
    env = rig.ask(question, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    return rig, env


def _why(rig: od._Space, sql: str) -> str | None:
    onto = load_space_ontology(rig.space_id)
    assert onto is not None
    con = duckdb.connect(str(rig.lake))
    try:
        violations = onto.verify(con)
    finally:
        con.close()
    return unverified_join_reason(
        sql, onto, violations, columns_of=relation_columns(onto, rig.lake)
    )


def _assert_join_abstain(env: dict[str, Any], rig: od._Space, figures: tuple[int, ...]) -> None:
    """ABSTAIN naming the join rule: no row, no value, no figure in the text (E9)."""
    text = od._assert_abstain(env, "")
    gap = next(line for line in text.splitlines() if "gap: " in line)
    assert "unverified_join" in gap, gap
    assert "unverified_join:" in od._reasons(env) + gap, env.get("assumptions")
    for figure in figures:
        assert str(figure) not in text, (figure, text)
    assert "total=" not in text and "The total is" not in text, text
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(od.JOIN_HOLE_SHAPES))
def test_one_table_read_twice_abstains(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql, wrong, true = od.JOIN_HOLE_SHAPES[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    # The danger is real: the statement runs and returns the wrong figure.
    assert od._oracle(rig.lake, sql) == [{"total": wrong}]
    assert od._oracle(rig.lake, TRUE_SQL[true]) == [{"total": true}]
    assert wrong != true
    _assert_join_abstain(env, rig, (wrong,))
    assert (_why(rig, sql) or "").startswith("unverified_join:")


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(od.JOIN_HOLE_CONTROLS))
def test_controls_refused_before_still_abstain(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql, wrong, true = od.JOIN_HOLE_CONTROLS[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    assert od._oracle(rig.lake, sql) == [{"total": wrong}] and wrong != true
    _assert_join_abstain(env, rig, (wrong,))


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(od.JOIN_HOLE_ONE_READ))
def test_one_read_of_one_table_still_answers(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql = od.JOIN_HOLE_ONE_READ[name]
    question = Q if "total" in sql else "List the enrollments"
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    oracle = od._oracle(rig.lake, sql)
    assert oracle, name
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert od._multiset(env["rows"]) == od._multiset(oracle)
    text = str(env.get("text") or "")
    first = oracle[0]
    if door == "contract":
        assert f"The total is {next(iter(first.values()))}." in text, text
    else:
        assert ", ".join(f"{k}={v}" for k, v in first.items()) in text, text
        assert rig.cortex.executed == [sql]
    assert _why(rig, sql) is None


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(NEWLY_ABSTAIN))
def test_one_table_statements_that_now_abstain_are_named(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """The price of the rule, pinned so it is never silent (issue clause 6)."""
    question, sql = NEWLY_ABSTAIN[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    assert od._oracle(rig.lake, sql), "the statement runs and returns rows"
    _assert_join_abstain(env, rig, ())
    assert (_why(rig, sql) or "").startswith("unverified_join:")


def test_two_reads_of_one_base_table_are_judged_as_before(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """A threshold over the same column names the table twice: the existing rule allows it."""
    sql = f"SELECT COUNT(*) AS total FROM {E} WHERE students > (SELECT MIN(students) FROM {E})"
    rig, env = _ask("generate", sql, tmp_path, monkeypatch, minter)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"))
    assert env["rows"] == [{"total": 3}] and "total=3" in str(env.get("text") or "")
    assert _why(rig, sql) is None
