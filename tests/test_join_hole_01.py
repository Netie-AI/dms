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
    "cte_rows_at_its_own_maximum": (
        "Which school has the most students?",
        f"WITH c AS (SELECT school_id, SUM(CAST(students AS INTEGER)) AS n FROM {E} GROUP BY school_id) SELECT school_id, n FROM c WHERE n = (SELECT MAX(n) FROM c)",
    ),
    "cte_cross_joined_to_its_own_total": (
        "Show each school's share of students",
        f"WITH c AS (SELECT school_id, SUM(CAST(students AS INTEGER)) AS n FROM {E} GROUP BY school_id), t AS (SELECT SUM(n) AS total FROM c) SELECT c.school_id, c.n * 100 / t.total AS pct FROM c CROSS JOIN t ORDER BY c.school_id",
    ),
    "constant_union_in_a_derived_table": (
        Q,
        f"SELECT SUM(n) AS total FROM (SELECT CAST(students AS INTEGER) AS n FROM {E} UNION ALL SELECT 0)",
    ),
    # A function sqlglot does not type cannot be proven to keep the row count
    # (unlist and generate_subscripts are such functions). EXTRACT / YEAR are typed.
    "untyped_function_in_projection": (
        Q,
        f"SELECT SUM(CAST(students AS INTEGER)) AS total, date_part('year', DATE '2024-05-01') AS y FROM {E}",
    ),
    "untyped_function_in_a_listing": (
        "List the enrollments",
        f"SELECT enrollment_id, hash(students) AS h FROM {E} ORDER BY enrollment_id",
    ),
    "untyped_function_in_order_by": (
        "List the enrollments",
        f"SELECT enrollment_id, students FROM {E} ORDER BY dayname(DATE '2024-05-01'), enrollment_id",
    ),
    "untyped_function_inside_a_window": (
        "List the enrollments",
        f"SELECT enrollment_id, SUM(date_part('year', DATE '2024-05-01')) OVER () AS y FROM {E} ORDER BY enrollment_id",
    ),
    # The generator doubles the subquery's SUM (60, not 30), so the filter keeps every row.
    "row_generator_in_a_where_subquery": (
        Q,
        f"SELECT SUM(CAST(students AS INTEGER)) AS total FROM {E} WHERE CAST(students AS INTEGER) < (SELECT SUM(k) FROM (SELECT 30 AS k, unlist([1, 2]) AS u))",
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


#: Shapes a DuckDB below 2 refuses to bind, with the engine's own words. DuckDB 2
#: runs them and returns the wrong figure in ``od.JOIN_HOLE_SHAPES``. The repo
#: pins ``duckdb>=1.1,<2`` (F-TBD-28), so on the pinned engine the refusal is the
#: proof that no figure can be served from the statement. The join rule abstains
#: on the envelope on both engines, and both outcomes are asserted: no skip.
ENGINE_REFUSES_BELOW_2: dict[str, str] = {"unlist_in_group_by": "UNNEST not supported here"}


def _engine_refuses(name: str) -> bool:
    """True when this DuckDB cannot run the named shape at all."""
    return name in ENGINE_REFUSES_BELOW_2 and int(duckdb.__version__.split(".")[0]) < 2


def _ask(
    door: str,
    sql: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    question: str = Q,
    contract_rows: list[dict[str, Any]] | None = None,
) -> tuple[od._Space, dict[str, Any]]:
    """Carry ``sql`` to the envelope through one door.

    ``contract_rows`` are the rows the contract ask claims for ``sql``. A caller
    passes them only when this engine refuses to run ``sql`` itself: the door
    must refuse the statement whatever rows come back with it.
    """
    if door == "loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = od._space(tmp_path, monkeypatch, minter)
    if door != "contract":
        return rig, rig.ask(question, {"query_sql": sql, "plan_source": "ontology_plan"})
    rows = od._oracle(rig.lake, sql) if contract_rows is None else contract_rows
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
    refused = _engine_refuses(name)
    # Where this engine will not run the statement, the contract ask still claims
    # the figure DuckDB 2 returns: the door refuses the SQL, it does not trust rows.
    rig, env = _ask(
        door,
        sql,
        tmp_path,
        monkeypatch,
        minter,
        contract_rows=[{"total": wrong}] if refused else None,
    )
    if refused:
        # DuckDB below 2 refuses to bind it, so no figure can be served from it here.
        with pytest.raises(duckdb.BinderException, match=ENGINE_REFUSES_BELOW_2[name]):
            od._oracle(rig.lake, sql)
    else:
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
    why = _why(rig, sql) or ""
    assert why.startswith("unverified_join:")
    if "unproven_function" in why:
        # The reason names the function and reaches the envelope whole.
        fn = why.split("(")[1].split(")")[0]
        assert why == od.UNPROVEN.format(fn) and fn == fn.lower()
        assert why in od._reasons(env) + str(env.get("text") or ""), (why, env.get("assumptions"))


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


@pytest.mark.parametrize("door", DOORS)
def test_statement_reading_no_table_abstains(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """``SELECT 42`` is not one read of one base table (it abstained ``ungranted`` before)."""
    sql = "SELECT 42 AS total"
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True, (env["badge"], env.get("text"))
    assert env["rows"] == [] and env["values"] == []
    assert "42" not in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == []
    assert _why(rig, sql) == "unverified_join:no_table"


# --- an EXISTS over a second table that names no link (JOIN-HOLE-01, #527) ----------

S, D = od.SCHOOLS, od.DISTRICTS
#: The question asks for the North district's schools (3). Each statement reads a
#: second table through an EXISTS that is not tied to the outer row by a verified
#: link, so the EXISTS is one constant TRUE or FALSE: it filters nothing, and the
#: count of every school (4) is served as the North count.
UNCORRELATED_EXISTS: dict[str, str] = {
    "exists_filtered": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE EXISTS (SELECT 1 FROM {D} d WHERE d.name = 'North')",
    "not_exists_filtered": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE NOT EXISTS (SELECT 1 FROM {D} d WHERE d.name = 'East')",
    "exists_unfiltered": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE EXISTS (SELECT 1 FROM {D} d)",
    "exists_same_table": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE EXISTS (SELECT 1 FROM {S} s2 WHERE s2.name = 'Alder')",
    "exists_beside_a_filter": f"SELECT COUNT(*) AS school_count FROM {S} s WHERE s.name <> 'Zed' AND EXISTS (SELECT 1 FROM {D} d WHERE d.name = 'North')",
}
CORRELATED_EXISTS = (
    f"SELECT COUNT(*) AS school_count FROM {S} s WHERE EXISTS "
    f"(SELECT 1 FROM {D} d WHERE d.district_id = s.district_id AND d.name = 'North')"
)


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(UNCORRELATED_EXISTS))
def test_exists_without_a_verified_link_abstains(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    sql = UNCORRELATED_EXISTS[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, od.VERIFIED_Q)
    # The danger is real: every school is counted, not the North district's three.
    assert od._oracle(rig.lake, sql) == [{"school_count": 4}]
    assert od._oracle(rig.lake, od.VERIFIED_SQL) == [{"school_count": 3}]
    text = od._assert_abstain(env, "")
    assert "unverified_join" in text, text
    assert "school_count=4" not in text and "The total is 4" not in text, text
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"
    assert _why(rig, sql) == "unverified_join:uncorrelated_subquery"


@pytest.mark.parametrize("door", DOORS)
def test_exists_over_the_verified_link_still_answers(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    rig, env = _ask(door, CORRELATED_EXISTS, tmp_path, monkeypatch, minter, od.VERIFIED_Q)
    assert od._oracle(rig.lake, CORRELATED_EXISTS) == [{"school_count": 3}]
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False and env["rows"] == [{"school_count": 3}]
    text = str(env.get("text") or "")
    assert ("The total is 3." if door == "contract" else "school_count=3") in text, text
    assert _why(rig, CORRELATED_EXISTS) is None


# --- routed by the parent orchestrator, 2026-11-20 (#527 class, P0) -------------------

#: name -> (question, sql, the figure DuckDB returns, the truth). Each read a
#: second table in a position the allowlist did not inspect, and was served.
ROUTED_HOLES: dict[str, tuple[str, str, Any, Any]] = {
    # A subquery in OFFSET inside an allowed same-column threshold: never checked.
    "offset_subquery_reads_a_second_table": (
        "What is the total of the school ids?",
        f"SELECT SUM(CAST(school_id AS INTEGER)) AS t FROM {S} WHERE school_id <= (SELECT MAX(school_id) FROM {S} OFFSET (SELECT COUNT(*) FROM {E}))",
        None,
        10,
    ),
    "limit_subquery_reads_a_second_table": (
        "What is the total of the school ids?",
        f"SELECT SUM(CAST(school_id AS INTEGER)) AS t FROM {S} WHERE school_id <= (SELECT MAX(school_id) FROM {S} LIMIT (SELECT COUNT(*) - 4 FROM {E}))",
        None,
        10,
    ),
    # COLUMNS() names the parent column without a Column node: the fan trap missed it.
    "columns_selector_counts_the_parent": (
        "How many districts have schools?",
        f"SELECT COUNT(COLUMNS('^name$')) AS t FROM {D} d JOIN {S} s ON s.district_id = d.district_id",
        4,
        2,
    ),
    # The orchestrator's exact SQL for the uncorrelated EXISTS (c634dcb).
    "exists_uncorrelated_exact": (
        Q,
        f"SELECT SUM(CAST(e.students AS INTEGER)) AS t FROM {E} e WHERE EXISTS (SELECT 1 FROM {S} s WHERE s.name = 'Alder')",
        107,
        30,
    ),
}


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(ROUTED_HOLES))
def test_routed_join_holes_abstain(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    question, sql, wrong, true = ROUTED_HOLES[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    # The danger is real: the statement runs and its figure is not the truth.
    assert od._oracle(rig.lake, sql) == [{"t": wrong}] and wrong != true
    text = od._assert_abstain(env, "")
    # The generate door refuses a statement citing enrollments one gate earlier:
    # its link to schools is broken in this fixture (ontology_unverified). The
    # loop and contract doors reach the join rule, and those served on the parent.
    assert "unverified_join" in text or "ontology_unverified" in text, text
    if wrong is not None:
        assert f"t={wrong}" not in text and f"The total is {wrong}" not in text, text
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"
    assert (_why(rig, sql) or "").startswith("unverified_join:")


# --- verifier round on the routed holes (#527 class, P0, on main before this track) ---

#: An ORDER BY inside an aggregate was taken as a safe position for a parent
#: column, so the fan trap never saw it. name -> (question, sql, served, truth).
ORDERED_AGG_HOLES: dict[str, tuple[str, str, Any, Any]] = {
    "count_parent_ordered_inside_the_aggregate": (
        "How many districts have schools?",
        f"SELECT COUNT(d.name ORDER BY s.name) AS t FROM {S} s JOIN {D} d ON s.district_id = d.district_id",
        4,
        2,
    ),
    "count_parent_key_ordered_inside_the_aggregate": (
        "How many districts have schools?",
        f"SELECT COUNT(d.district_id ORDER BY s.school_id) AS t FROM {S} s JOIN {D} d ON s.district_id = d.district_id",
        4,
        2,
    ),
    "string_agg_of_the_parent_ordered": (
        "List the districts that have schools",
        f"SELECT string_agg(d.name, ',' ORDER BY s.name) AS t FROM {S} s JOIN {D} d ON s.district_id = d.district_id",
        "North,North,South,North",
        "North,South",
    ),
}

#: A row generator in the projection of a statement that reaches the join path
#: (a join, or a same-column threshold subquery) was never checked there.
#: name -> (question, sql, rows served, true row count).
JOIN_PATH_GENERATORS: dict[str, tuple[str, str, int, int]] = {
    "unnest_in_a_join_listing": (
        "List the schools in the North district",
        f"SELECT s.name, UNNEST([1, 2]) AS u FROM {S} s JOIN {D} d ON s.district_id = d.district_id WHERE d.name = 'North'",
        6,
        3,
    ),
    "unnest_behind_a_same_column_threshold": (
        "List the schools",
        f"SELECT name, UNNEST([1, 2]) AS u FROM {S} WHERE school_id <= (SELECT MAX(school_id) FROM {S})",
        8,
        4,
    ),
}


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(ORDERED_AGG_HOLES))
def test_parent_column_ordered_inside_an_aggregate_abstains(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    question, sql, wrong, true = ORDERED_AGG_HOLES[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    assert od._oracle(rig.lake, sql) == [{"t": wrong}] and wrong != true
    text = od._assert_abstain(env, "")
    assert "unverified_join" in text, text
    assert str(wrong) not in text, text
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"
    assert (_why(rig, sql) or "").startswith("unverified_join:fan_trap")


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(JOIN_PATH_GENERATORS))
def test_row_generator_on_the_join_path_abstains(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    question, sql, served, true = JOIN_PATH_GENERATORS[name]
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, question)
    assert len(od._oracle(rig.lake, sql)) == served and served != true
    od._assert_abstain(env, "")
    assert rig.cortex.executed == [], "refused SQL reached Cortex submit"
    assert _why(rig, sql) == "unverified_join:row_generator"


@pytest.mark.parametrize("door", DOORS)
def test_parent_column_in_the_statements_order_by_still_answers(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
    store: OntologyStore,  # noqa: F811
) -> None:
    """Ordering the result by a parent column repeats nothing: it still answers."""
    sql = (
        f"SELECT d.name AS district, COUNT(*) AS n FROM {S} s JOIN {D} d "
        "ON s.district_id = d.district_id GROUP BY d.name ORDER BY d.name"
    )
    rig, env = _ask(door, sql, tmp_path, monkeypatch, minter, "How many schools are in each district?")
    truth = [{"district": "North", "n": 3}, {"district": "South", "n": 1}]
    assert od._oracle(rig.lake, sql) == truth
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert od._multiset(env["rows"]) == od._multiset(truth)
    assert _why(rig, sql) is None


# --- demo Spaces meet the join rule (routed P0, #527 class) --------------------------

import test_connect_ask_03 as ca  # noqa: E402

DEMO_Q = "What is the total inventory quantity?"
DEMO_TRUE = "SELECT SUM(quantity_kg) AS total FROM inventory"
#: name -> sql. In a demo Space rule 16 never ran: each was served with a
#: multiplied figure. Now checked against the demo pack's declared ontology.
DEMO_JOIN_HOLES: dict[str, str] = {
    "cross_product_with_locations": "SELECT SUM(i.quantity_kg) AS total FROM inventory i, locations l",
    "join_on_an_undeclared_column": "SELECT SUM(i.quantity_kg) AS total FROM inventory i JOIN locations l ON i.sku = l.name",
    "fan_trap_over_a_declared_link": "SELECT SUM(l.capacity_kg) AS total FROM inventory i JOIN locations l ON i.location_id = l.location_id",
    # One table read in two places (demo verifier round): issue shape 6 served 55230.
    "issue_shape_6_cte_read_twice": "WITH c AS (SELECT quantity_kg AS x FROM inventory) SELECT (SELECT SUM(x) FROM c) * COUNT(*) AS total FROM c",
    "scalar_total_times_the_row_count": "SELECT (SELECT SUM(quantity_kg) FROM inventory) * COUNT(*) AS total FROM inventory",
    "total_times_a_scalar_row_count": "SELECT SUM(quantity_kg) * (SELECT COUNT(*) FROM inventory) AS total FROM inventory",
    "cte_total_times_a_scalar_cte_count": "WITH c AS (SELECT quantity_kg AS x FROM inventory) SELECT SUM(x) * (SELECT COUNT(*) FROM c) AS total FROM c",
}


def _demo(sql: str, door: str, space_id: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, question: str = DEMO_Q) -> tuple[dict[str, Any], Any, Any]:  # noqa: F811
    client, cortex, _app, lake = ca._app(tmp_path, monkeypatch, minter)
    rig = ca._Rig(client, cortex, space_id, lake)
    figure = ca._oracle(lake, sql)
    if door == "contract":

        def _contract(req: Any) -> AskResponse:
            return AskResponse(
                answer=f"The total is {figure[0]['total'] if figure else None}.",
                abstained=False,
                badge="generated",
                route="generated",
                sql_used=sql,
                rows=figure,
            )

        monkeypatch.setattr(cortex, "ask", _contract)
        return rig.ask(question, {}), figure, ca._oracle(lake, DEMO_TRUE)
    return rig.ask(question, ca._gen(sql)), figure, ca._oracle(lake, DEMO_TRUE)


@pytest.mark.parametrize("door", ["generate", "contract"])
@pytest.mark.parametrize("space_id", [None, ca.DEMO_FINANCE])
@pytest.mark.parametrize("name", sorted(DEMO_JOIN_HOLES))
def test_a_demo_space_join_meets_the_demo_ontology(
    name: str,
    space_id: str | None,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    sql = DEMO_JOIN_HOLES[name]
    env, figure, truth = _demo(sql, door, space_id, tmp_path, monkeypatch, minter)
    assert figure and figure != truth, (figure, truth)  # the figure is not the table's
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True, (env["badge"], env.get("text"))
    assert env["rows"] == [] and env["values"] == []
    text = str(env.get("text") or "")
    assert "unverified_join" in od._reasons(env) + " " + text, (env.get("assumptions"), text)
    assert str(figure[0]["total"]) not in text, text


@pytest.mark.parametrize("door", ["generate", "contract"])
@pytest.mark.parametrize("space_id", [None, ca.DEMO_FINANCE])
def test_a_demo_space_join_over_a_declared_link_still_answers(
    space_id: str | None,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,  # noqa: F811
) -> None:
    sql = (
        "SELECT l.name AS location, SUM(i.quantity_kg) AS total FROM inventory i "
        "JOIN locations l ON i.location_id = l.location_id GROUP BY l.name ORDER BY l.name"
    )
    env, figure, _truth = _demo(
        sql, door, space_id, tmp_path, monkeypatch, minter, "What is the inventory quantity at each location?"
    )
    assert figure, "the demo lake has inventory at locations"
    assert env["badge"] != "ABSTAIN" and env["abstained"] is False, (env.get("text"), env.get("assumptions"))
    assert od._multiset(env["rows"]) == od._multiset(figure)
