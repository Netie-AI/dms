"""The confirmed lane states one figure under a confirmed basis, so no condition may vanish or
appear between the question and the number (dms#284). Regressions found by the confirmed-lane
adversary round, each at the customer envelope (``POST /v1/chat/ask`` through
``assert_envelope_valid``) against an independent oracle query on the lake, never the SQL alone
(CLAUDE.md rules 10 and 10a):

* F1 the source SCHEMA word is not a statement about the data: ``bronze.public_schools`` is the
  table ``schools`` of schema ``public``, so "public schools" is a condition, not the name of the
  table (it was answered by the total over ALL schools). Same for the schema word of a filter or
  group-by table, and for any schema (``hr``, ``archive``, ``dbo``).
* F2 two conditions on one column are not a condition a question states ("North and South" ANDed
  two equalities into zero rows and ``COUNT`` printed 0); a literal used by two filters must be
  stated twice.
* F3 a source that was re-pulled TRUNCATED after the steward confirmed is not the source that was
  confirmed: the measure is not usable, it does not answer a smaller total under the same badge.
* F4 the plan's own ``limit`` (Cortex's, or the default 50) is a row cap the question never
  stated: a grouped answer states every group or abstains.
* F5 a plan-supplied ``via`` that picks one of two declared links is a choice no word of the
  question made. F5b the same class for a ROUTE: two paths of different length to one dimension
  (a direct foreign key and a chain through another table) are two readings of "in North", and
  neither a ``via`` nor the layer's shortest-path default may pick one for the customer.
* F2b "North and South" over a district filter and a region filter is the same symptom as F2 on
  two DIFFERENT columns: a conjunction between two stated values is a list, the plan ANDs them.

Non-confirmed lanes are untouched; this file only reads the confirmed lane.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import test_ingest_fixes_277 as ing
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import db_connector as dbc
from dms_executor import generative_ask as ga
from dms_executor import measure_use as mu
from dms_executor.ontology import Measure, MeasureProvenance
from dms_executor.space_ontology import set_ontology_store
from test_connect_ask_02 import _assert_answer, _assert_basis, _multiset, _reasons, _Rigged
from test_ingest_fixes_277 import _oracle
from test_onto_confirm_01 import _Env, _lake_exec, _Ledger
from test_onto_derive_01 import _assert_abstain, make_minter

BUDGET = "schools_budget_sum"
SCOUNT = "schools_count"
TOTAL_Q = "What is the total budget of all schools?"
PUBLIC_Q = "What is the total budget of public schools?"
ALL_BUDGET = Decimal("360.75")
SCHOOLS = "bronze.public_schools"
DISTRICTS = "bronze.public_districts"


def _rel(schema: str) -> tuple[str, str]:
    return f"bronze.{schema}_schools", f"bronze.{schema}_districts"


def _lake(schema: str = "public") -> dict[str, Any]:
    """The typed schools data of the 277 rig plus a ``kind`` and a ``region`` column, in any
    source schema. Schools: Alder 100.50 public North, Birch 200.25 private North, Cedar 50.00
    public South, Dogwood 10.00 private north (lower-case region)."""
    q = f'"{schema}"'
    return {
        "tables": [(schema, "districts"), (schema, "schools")],
        "data": {
            f'{q}."districts"': (["district_id", "name"], [[1, "North"], [2, "South"]]),
            f'{q}."schools"': (
                ["school_id", "district_id", "name", "budget", "kind", "region"],
                [
                    [1, 1, "Alder", Decimal("100.50"), "public", "North"],
                    [2, 1, "Birch", Decimal("200.25"), "private", "North"],
                    [3, 2, "Cedar", Decimal("50.00"), "public", "South"],
                    [4, 1, "Dogwood", Decimal("10.00"), "private", "north"],
                ],
            ),
        },
        "types": {
            f"{schema}.districts": [
                ("district_id", "integer", 32, 0),
                ("name", "text", None, None),
            ],
            f"{schema}.schools": [
                ("school_id", "integer", 32, 0),
                ("district_id", "integer", 32, 0),
                ("name", "character varying", None, None),
                ("budget", "numeric", 10, 2),
                ("kind", "text", None, None),
                ("region", "text", None, None),
            ],
        },
        "pks": [(schema, "districts", "district_id", 1), (schema, "schools", "school_id", 1)],
        "fks": [
            (
                "schools_district_id_fkey", schema, "schools", "district_id",
                schema, "districts", "district_id", 1,
            )
        ],
    }  # fmt: skip


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


def build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: Any,
    store: OntologyStore,
    *,
    schema: str = "public",
    lake: dict[str, Any] | None = None,
    also: tuple[str, ...] = (),
) -> _Rigged:
    held = lake if lake is not None else _lake(schema)
    monkeypatch.setattr(ing, "PKS", held["pks"])
    monkeypatch.setattr(ing, "FKS", held["fks"])
    rig = ing._Rig(tmp_path, monkeypatch, minter)
    space = rig.space("guards")
    rig.ingest(space, tables=held["tables"], data=held["data"], types=held["types"])
    env = _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())
    rigged = _Rigged(env)
    rigged.confirm(BUDGET, SCOUNT, *also)
    return rigged


@pytest.fixture()
def r(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> _Rigged:
    return build(tmp_path, monkeypatch, minter, store)


def ranked(rigged: _Rigged, question: str, measure: str = BUDGET) -> dict[str, Any]:
    """Cortex ranked the measure and sent no plan and no SQL: the router builds the plan."""
    return rigged.space.ask(
        question, {"plan_source": "ontology_plan", "ontology": {"metrics": [{"id": measure}]}}
    )


def typed(
    rigged: _Rigged,
    question: str,
    measure: str,
    *,
    group_by: tuple[tuple[str, str], ...] = (),
    filters: tuple[tuple[Any, ...], ...] = (),
    **plan_fields: Any,
) -> dict[str, Any]:
    """A typed plan with extra ``query_plan`` fields Cortex may send (``limit``, ``via``)."""
    plan = {
        "measure": measure,
        "group_by": [list(g) for g in group_by],
        "filters": [list(f) for f in filters],
        **plan_fields,
    }
    return rigged.space.ask(question, {"query_plan": plan, "plan_source": "ontology_plan"})


def _refused(rigged: _Rigged, env: dict[str, Any], *needles: str) -> str:
    """The envelope is a named abstain: no figure, no basis, no rows, nothing executed."""
    text = _assert_abstain(env, "no_declared_measure")
    assert "measure_basis" not in env
    assert rigged.executed == [] and rigged.rig.cortex.asks == []
    why = _reasons(env)
    for needle in needles:
        assert needle in why, (needle, why)
    return text


def _sum(rigged: _Rigged, where: str = "TRUE", schema: str = "public") -> list[dict[str, Any]]:
    return _oracle(
        rigged.rig.lake, f"SELECT SUM(budget) AS v FROM bronze.{schema}_schools WHERE {where}"
    )


# ============================================================ F1: the source schema word


def test_f1_public_schools_is_a_condition_not_the_name_of_the_table(r: _Rigged) -> None:
    """The adversary's repro. The lake has kind = 'public' and kind = 'private' schools; the
    question says "public schools"; the plan carries no filter. The total over all schools
    (360.75) is a different number than the public ones (150.50)."""
    assert _sum(r) == [{"v": ALL_BUDGET}]
    assert _sum(r, "kind = 'public'") == [{"v": Decimal("150.50")}]
    env = r.plan(PUBLIC_Q, BUDGET)
    text = _refused(r, env, "fit_disagrees:unbound_constraint", "'public'")
    assert "360.75" not in text


def test_f1_the_ranking_route_with_no_plan_from_cortex_abstains_too(r: _Rigged) -> None:
    env = ranked(r, PUBLIC_Q)
    text = _refused(r, env, "fit_disagrees:unbound_constraint", "'public'")
    assert "360.75" not in text


def test_f1_control_the_condition_bound_to_its_column_answers_the_public_figure(
    r: _Rigged,
) -> None:
    env = r.plan(PUBLIC_Q, BUDGET, filters=((SCHOOLS, "kind", "=", "public"),))
    _assert_answer(env, _sum(r, "kind = 'public'"), "150.5")
    assert "360.75" not in str(env["text"])
    _assert_basis(env, r, BUDGET)


def test_f1_the_schema_word_of_a_filter_table_is_not_vocabulary_either(r: _Rigged) -> None:
    """``named`` held the words of the FILTER table's relation: 'public' again."""
    districts = DISTRICTS
    env = r.plan(
        "What is the total budget of public schools in North?",
        BUDGET,
        filters=((districts, "name", "=", "North"),),
    )
    _refused(r, env, "fit_disagrees:unbound_constraint", "'public'")
    # the same question without the schema word is the North total
    env = r.plan(
        "What is the total budget of schools in North?",
        BUDGET,
        filters=((districts, "name", "=", "North"),),
    )
    _assert_answer(env, _sum(r, "district_id = 1"), "310.75")


def test_f1_the_schema_word_of_a_group_by_table_is_not_a_breakdown(r: _Rigged) -> None:
    """ "by public" is not "by district": the group-by table's relation read ``public``. (Another
    gate may speak first on this wording; the fit gate itself is pinned in the unit test.)"""
    env = r.plan(
        "What is the total budget of schools by public?", BUDGET, group_by=((DISTRICTS, "name"),)
    )
    assert env["abstained"] is True and env["rows"] == [] and "measure_basis" not in env
    assert r.executed == []
    env = r.plan(
        "What is the total budget of schools by district?",
        BUDGET,
        group_by=((SCHOOLS, "district_id"),),
    )
    oracle = _oracle(
        r.rig.lake,
        "SELECT district_id, SUM(budget) AS v FROM bronze.public_schools GROUP BY district_id",
    )
    _assert_answer(env, oracle, "310.75")


@pytest.mark.parametrize("schema", ["hr", "archive", "dbo", "sales"])
def test_f1_any_source_schema_is_not_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore, schema: str
) -> None:
    rigged = build(tmp_path, monkeypatch, minter, store, schema=schema)
    schools, _districts = _rel(schema)
    assert schools == f"bronze.{schema}_schools"
    env = rigged.plan(f"What is the total budget of {schema} schools?", BUDGET)
    _refused(rigged, env, "fit_disagrees:unbound_constraint", f"'{schema}'")
    env = rigged.plan("What is the total budget of all schools?", BUDGET)
    _assert_answer(env, _sum(rigged, schema=schema), "360.75")


def test_f1_unit_the_tables_map_is_the_only_source_of_a_relation_word() -> None:
    pool = {BUDGET: _measure(BUDGET, SCHOOLS)}
    filters = [(DISTRICTS, "name", "=", "North")]
    tables = {SCHOOLS: "schools", DISTRICTS: "districts"}
    # the schema word, of the grain table or of a filter table, is not a word the plan covers
    for q, extra in (
        ("What is the total budget of public schools?", {"filters": []}),
        ("What is the total budget of public schools in North?", {"filters": filters}),
    ):
        got = mu.measure_fit(q, pool, tables=tables, **extra)
        assert (got.ok, got.reason) == (False, "unbound_constraint"), (q, got)
        assert "'public'" in got.detail, got
    # ... nor a breakdown: "by public" is not "by district" (the group-by table's schema word)
    by = [(DISTRICTS, "name")]
    q = "What is the total budget of schools by public?"
    got = mu.measure_fit(q, pool, grouped_by=by, filters=[], tables=tables)
    assert (got.ok, got.reason) == (False, "qualifier_cue"), got
    q = "What is the total budget of schools by district?"
    assert mu.measure_fit(q, pool, grouped_by=by, filters=[], tables=tables).ok is True
    ok = "What is the total budget of schools in North?"
    assert mu.measure_fit(ok, pool, filters=filters, tables=tables).ok is True
    # a relation with no source record contributes NO word (default deny): the schema word and
    # the table word are both unknown, so "by district" is not accounted for
    got = mu.measure_fit(q, pool, grouped_by=by, filters=[], tables={SCHOOLS: "schools"})
    assert (got.ok, got.reason) == (False, "qualifier_cue"), got
    got = mu.measure_fit(q, pool, grouped_by=by, filters=[])
    assert (got.ok, got.reason) == (False, "qualifier_cue"), got


# ============================================================ F2: two conditions on one column


def test_f2_two_equalities_on_one_column_are_not_what_north_and_south_states(r: _Rigged) -> None:
    districts = DISTRICTS
    oracle = _oracle(r.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")
    assert oracle == [{"v": 4}]
    env = r.plan(
        "How many schools are there in North and South?",
        SCOUNT,
        filters=((districts, "name", "=", "North"), (districts, "name", "=", "South")),
    )
    text = _refused(r, env, "fit_disagrees:unstated_filter")
    assert "schools_count=0" not in text
    # the reading the question does state: one IN list
    env = r.plan(
        "How many schools are there in North and South?",
        SCOUNT,
        filters=((districts, "name", "IN", ["North", "South"]),),
    )
    _assert_answer(env, oracle, "4")


def test_f2_two_ids_on_one_column_and_an_equality_with_an_in_list(r: _Rigged) -> None:
    schools = SCHOOLS
    env = r.plan(
        "How many schools are there with id 1 and 2?",
        SCOUNT,
        filters=((schools, "school_id", "=", 1), (schools, "school_id", "=", 2)),
    )
    _refused(r, env, "fit_disagrees:unstated_filter")
    env = r.plan(
        "How many schools are there with id 1 and 2?",
        SCOUNT,
        filters=((schools, "school_id", "IN", [1, 2]), (schools, "school_id", "=", 1)),
    )
    _refused(r, env, "fit_disagrees:unstated_filter")
    # the column is the same however it is spelled
    env = r.plan(
        "How many schools are there with id 1 and 2?",
        SCOUNT,
        filters=((schools, "school_id", "=", 1), (schools, "SCHOOL_ID", "=", 2)),
    )
    _refused(r, env, "fit_disagrees:unstated_filter")
    env = r.plan(
        "How many schools are there with school id 1 and 2?",
        SCOUNT,
        filters=((schools, "school_id", "IN", [1, 2]),),
    )
    _assert_answer(env, [{"v": 2}], "2")


def test_f2_a_literal_two_filters_use_must_be_stated_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    """ "North" is the district's name AND the region: the plan ANDs both. One mention of
    "North" states one of them; the other is a condition the plan added."""
    held = copy.deepcopy(_lake())
    held["data"]['"public"."schools"'][1][3][5] = "North"  # one spelling of the region
    r = build(tmp_path, monkeypatch, minter, store, lake=held)
    both = ((DISTRICTS, "name", "=", "North"), (SCHOOLS, "region", "=", "North"))
    env = r.plan("What is the total budget of schools in North?", BUDGET, filters=both)
    _refused(r, env, "fit_disagrees:unstated_filter", "North (needs 2 mentions, found 1)")
    # said twice, it is two statements, and the plan carries exactly two
    env = r.plan(
        "What is the total budget of schools in district name North in region North?",
        BUDGET,
        filters=both,
    )
    oracle = _sum(r, "district_id = 1 AND region = 'North'")
    assert oracle == [{"v": Decimal("310.75")}]
    _assert_answer(env, oracle, "310.75")


def test_f2_unit_the_accounting_counts_multiplicity() -> None:
    acc = mu.account_for_question
    q = "total budget of schools in North"
    two = [("o", "district", "=", "North"), ("o", "region", "=", "North")]
    assert acc(q, measure=BUDGET, grain="bronze.public_schools", filters=two).reason == (
        "unstated_filter"
    )
    # said twice, each time after its own column's name: two statements, two conditions
    q2 = "total budget of schools in North in region North"
    assert acc(q2, measure=BUDGET, grain="bronze.public_schools", filters=two).ok
    # one IN list never repeats a value
    assert acc(
        q,
        measure=BUDGET,
        grain="bronze.public_schools",
        filters=[("o", "a", "IN", ["North", "North"])],
    ).ok


# ============================================================ F2b: a conjunction across two columns

NORTH_AND_SOUTH = (
    (DISTRICTS, "name", "=", "North"),
    (SCHOOLS, "region", "=", "South"),
)


def _south_dogwood() -> dict[str, Any]:
    """Dogwood (district North) is in region South too, so ANDing the two literals matches ONE
    school: a plausible wrong figure, not only a zero."""
    held = copy.deepcopy(_lake())
    held["data"]['"public"."schools"'][1][3][5] = "South"
    return held


@pytest.mark.parametrize(
    "question",
    [
        "How many schools are there in North and South?",
        "How many schools are there in North, South?",
        "How many schools are there in North, and South?",
        "How many schools are there in North and in South?",
        "How many schools are there in North, how many in South?",
    ],
)
def test_f2b_two_values_joined_by_and_on_two_columns_are_a_list_not_two_conditions(
    r: _Rigged, question: str
) -> None:
    """The adversary's repro. The question names two places; the plan ANDs a district filter and
    a region filter, which matches no school, and COUNT printed 0 under the confirmed badge."""
    assert _oracle(r.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools") == [{"v": 4}]
    assert _oracle(
        r.rig.lake,
        "SELECT COUNT(*) AS v FROM bronze.public_schools "
        "WHERE district_id = 1 AND region = 'South'",
    ) == [{"v": 0}]
    env = r.plan(question, SCOUNT, filters=NORTH_AND_SOUTH)
    text = _refused(r, env, "fit_disagrees:unstated_filter")
    assert "schools_count=0" not in text and "Found 1 row" not in text


def test_f2b_the_anded_figure_can_be_a_plausible_nonzero_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    rigged = build(tmp_path, monkeypatch, minter, store, lake=_south_dogwood())
    anded = _oracle(
        rigged.rig.lake,
        "SELECT COUNT(*) AS v FROM bronze.public_schools "
        "WHERE district_id = 1 AND region = 'South'",
    )
    assert anded == [{"v": 1}], "one school is North AND South: the wrong figure is a believable 1"
    env = rigged.plan(
        "How many schools are there in North and South?", SCOUNT, filters=NORTH_AND_SOUTH
    )
    text = _refused(rigged, env, "fit_disagrees:unstated_filter")
    assert "schools_count=1" not in text


@pytest.mark.parametrize(
    ("question", "filters", "where", "figure"),
    [
        # one value list on one column is what "North and South" states
        (
            "How many schools are there in North and South?",
            ((DISTRICTS, "name", "IN", ["North", "South"]),),
            "district_id IN (1, 2)",
            "4",
        ),
        # a value of its own column between two values that share one: the join is a list, the
        # other literal is a condition introduced by a place and a verb, not by "and"
        (
            "How many public schools are there in district name North and South?",
            ((SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "IN", ["North", "South"])),
            "kind = 'public' AND district_id IN (1, 2)",
            "2",
        ),
        # two conditions on two columns, each stated with words between them and no conjunction
        (
            "How many public schools are there in district name North?",
            ((SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "=", "North")),
            "kind = 'public' AND district_id = 1",
            "1",
        ),
        (
            "How many schools are public in district name North?",
            ((SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "=", "North")),
            "kind = 'public' AND district_id = 1",
            "1",
        ),
        # a comma that ends a clause: a name of the plan stands between the two values
        (
            "In district name North, how many schools are public?",
            ((SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "=", "North")),
            "kind = 'public' AND district_id = 1",
            "1",
        ),
    ],
)
def test_f2b_controls_what_the_question_states_still_answers(
    r: _Rigged,
    question: str,
    filters: tuple[tuple[Any, ...], ...],
    where: str,
    figure: str,
) -> None:
    oracle = _oracle(r.rig.lake, f"SELECT COUNT(*) AS v FROM bronze.public_schools WHERE {where}")
    env = r.plan(question, SCOUNT, filters=filters)
    _assert_answer(env, oracle, figure)


def test_f2b_mutation_without_the_gate_the_anded_zero_is_stated_as_a_finding(
    r: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harm the gate prevents, shown by turning it off: 0 under the confirmed badge."""
    # every layer that refuses it: the list-join check, the ``and`` rule, the empty-set rule
    monkeypatch.setattr(mu, "_joined_values", lambda *a, **k: [])
    monkeypatch.setattr(mu, "_list_and", lambda *a, **k: True)
    monkeypatch.setattr(ga, "_empty_filter_reason", lambda *a, **k: None)
    monkeypatch.setattr(mu, "_columns_holding", lambda *a, **k: [])  # the binding rule
    env = r.plan("How many schools are there in North and South?", SCOUNT, filters=NORTH_AND_SOUTH)
    _assert_answer(env, [{"v": 0}], "schools_count=0")


def test_f2b_unit_a_conjunction_joins_values_of_different_filters_only() -> None:
    acc = mu.account_for_question
    grain = "bronze.public_schools"
    two = [("o", "district", "=", "North"), ("o", "region", "=", "South")]
    one = [("o", "district", "IN", ["North", "South"])]
    for q in (
        "total budget of schools in North and South",
        "total budget of schools in North, South",
        "total budget of schools in North, and South",
        "total budget of schools in North and in South",
        "total budget of schools in North, how many in South",
        "total budget of schools in South and North",
    ):
        got = acc(q, measure=BUDGET, grain=grain, filters=two)
        assert (got.ok, got.reason) == (False, "unstated_filter"), (q, got)
        assert acc(q, measure=BUDGET, grain=grain, filters=one).ok, q
    # nothing between them but a name of the plan, or a preposition: not a list
    for q in (
        "total budget of schools in North in region South",
        "total budget of schools in North and region South",
        "total budget of schools in district North and region South",
    ):
        assert acc(q, measure=BUDGET, grain=grain, filters=two).ok, q
    # two values side by side, or with a verb or a name between them, are not a list
    months = [("o", "month", "=", "March"), ("o", "year", "=", 2024)]
    for q in (
        "total budget of schools in March 2024",
        "total budget of schools in 2024 March",
        "total budget of schools in March of 2024",
    ):
        # not a list (no ``and``, no comma), but a number binds to its column only by a word of
        # that column standing before it (cluster A6): "2024" beside no "year" is refused
        got = acc(q, measure=BUDGET, grain=grain, filters=months)
        assert (got.ok, got.reason) == (False, "unbound_constraint"), (q, got)
        assert "2024" in got.unaccounted[0], got
    for q in (
        "total budget of schools in March in year 2024",
        "total budget of schools in March year 2024",
    ):
        assert acc(q, measure=BUDGET, grain=grain, filters=months).ok, q
    # ... but "and" or a comma between them is
    for q in (
        "total budget of schools in March and 2024",
        "total budget of schools in March, 2024",
    ):
        got = acc(q, measure=BUDGET, grain=grain, filters=months)
        assert (got.ok, got.reason) == (False, "unstated_filter"), (q, got)
    # a value used by two filters is not provably one list: joined, it is refused
    both = [("o", "district", "=", "North"), ("o", "region", "=", "North")]
    q = "total budget of schools in North and North"
    got = acc(q, measure=BUDGET, grain=grain, filters=both)
    assert (got.ok, got.reason) == (False, "unstated_filter"), got


# ============================================================ F3: a truncated re-pull


def _repull_truncated(rigged: _Rigged, monkeypatch: pytest.MonkeyPatch) -> None:
    real = dbc._fetch

    def capped(cfg: Any, con: Any, target: Any, *, max_rows: int) -> Any:
        cols, rows, _truncated = real(cfg, con, target, max_rows=max_rows)
        return cols, rows[:2], True  # the connector's row cap hit

    monkeypatch.setattr(dbc, "_fetch", capped)
    held = _lake()
    rigged.rig.ingest(
        rigged.env.space, tables=held["tables"], data=held["data"], types=held["types"]
    )
    monkeypatch.setattr(dbc, "_fetch", real)


def _registry(rigged: _Rigged) -> dict[str, bool]:
    rows = _oracle(rigged.rig.lake, "SELECT table_name, truncated FROM bronze._ingest_registry")
    return {row["table_name"]: bool(row["truncated"]) for row in rows}


def test_f3_a_truncated_repull_after_confirm_is_not_the_source_that_was_confirmed(
    r: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert r.plan(TOTAL_Q, BUDGET)["abstained"] is False  # the confirmed figure first
    _repull_truncated(r, monkeypatch)
    assert _registry(r)["public_schools"] is True, "the pull recorded that the cap was hit"
    r.rig.cortex.executed.clear()
    env = r.plan(TOTAL_Q, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert "measure_basis" not in env and "300.75" not in text
    assert f"{BUDGET} is not usable now: source_truncated" in _reasons(env), _reasons(env)
    # the ranking route: the pruned measure is not in the pool, so nothing ranks it
    env = ranked(r, TOTAL_Q)
    assert env["abstained"] is True and env["rows"] == [] and "measure_basis" not in env
    assert "300.75" not in str(env["text"]) and "300.75" not in str(env["values"])
    assert r.executed == [], "nothing ran against the capped rows"


def test_f3_the_registry_alone_is_enough_and_a_whole_repull_answers_again(
    r: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ingest record is read at ask time, not only the snapshot taken at derive time."""
    _lake_exec(r.rig.lake, "UPDATE bronze._ingest_registry SET truncated = TRUE")
    env = r.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert f"{BUDGET} is not usable now: source_truncated" in _reasons(env)
    # a whole pull of the same source puts it back: the refusal is state, not a latch
    held = _lake()
    r.rig.ingest(r.env.space, tables=held["tables"], data=held["data"], types=held["types"])
    assert _registry(r)["public_schools"] is False
    _assert_answer(r.plan(TOTAL_Q, BUDGET), _sum(r), "360.75")


def test_f3_no_readable_ingest_record_means_completeness_is_unproven(r: _Rigged) -> None:
    """prune, called as the ask calls it: a relation whose pull nobody recorded (or a registry
    that cannot be read) is not shown to be whole, so the measure does not answer."""
    from dms_executor.space_ontology import load_space_ontology

    allowed = {SCHOOLS, DISTRICTS}
    onto = load_space_ontology(r.env.space, r.env.store)
    assert onto is not None and BUDGET in onto.measures
    kept = mu.prune_unusable_measures([onto], [], r.rig.lake, allowed)
    assert kept == {} and onto.measures[BUDGET].provenance is not None
    assert onto.measures[BUDGET].provenance.columns_checked is True, "control: it passes whole"

    _lake_exec(
        r.rig.lake, "DELETE FROM bronze._ingest_registry WHERE table_name = 'public_schools'"
    )
    gone = load_space_ontology(r.env.space, r.env.store)
    assert gone is not None
    assert mu.prune_unusable_measures([gone], [], r.rig.lake, allowed) == {
        BUDGET: "lake_unreadable",
        SCOUNT: "lake_unreadable",
    }
    _lake_exec(r.rig.lake, "DROP TABLE bronze._ingest_registry")
    none = load_space_ontology(r.env.space, r.env.store)
    assert none is not None
    assert set(mu.prune_unusable_measures([none], [], r.rig.lake, allowed).values()) == {
        "lake_unreadable"
    }


def test_f3_the_snapshot_flag_alone_is_enough_too() -> None:
    """prune reads ``Ontology.truncated`` (the derive-time flag) as well as the registry, and it
    needs no lake for that."""
    grain = "bronze.public_schools"
    onto = _onto_with(_measure(BUDGET, grain))
    onto.truncated[grain] = True
    removed = mu.prune_unusable_measures([onto], [], None, {grain})
    assert removed == {BUDGET: "source_truncated"}
    assert BUDGET not in onto.measures
    assert mu.measure_exclusions(onto) == {BUDGET: "source_truncated"}
    assert mu.EXCL_SOURCE_TRUNCATED in mu.EXCLUSION_REASONS


def test_f3_a_truncated_parent_is_caught_by_the_orphan_check_and_leaves_the_whole_grain(
    r: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the grain's own completeness is a measure-level fact. A parent the cap cut leaves
    child rows pointing at keys that did not land, and ``verify()`` already refuses the link
    (``fk_intact``); the measure over the whole, uncut grain keeps answering what it can."""
    real = dbc._fetch

    def cap_districts(cfg: Any, con: Any, target: Any, *, max_rows: int) -> Any:
        cols, rows, truncated = real(cfg, con, target, max_rows=max_rows)
        return (cols, rows[:1], True) if target.name == "districts" else (cols, rows, truncated)

    monkeypatch.setattr(dbc, "_fetch", cap_districts)
    held = _lake()
    r.rig.ingest(r.env.space, tables=held["tables"], data=held["data"], types=held["types"])
    assert _registry(r) == {"public_districts": True, "public_schools": False}
    env = r.plan(
        "What is the total budget of schools in North?",
        BUDGET,
        filters=((DISTRICTS, "name", "=", "North"),),
    )
    assert env["abstained"] is True and env["rows"] == [] and "measure_basis" not in env
    _assert_answer(r.plan(TOTAL_Q, BUDGET), _sum(r), "360.75")


# ============================================================ F4: the plan's own row cap


def test_f4_a_plan_limit_the_question_never_stated_does_not_drop_a_group(r: _Rigged) -> None:
    oracle = _oracle(
        r.rig.lake,
        "SELECT region, SUM(budget) AS v FROM bronze.public_schools GROUP BY region",
    )
    assert len(oracle) == 3  # North, South, north
    env = typed(
        r, "What is the total budget of schools by region?", BUDGET,
        group_by=((SCHOOLS, "region"),), limit=1,
    )  # fmt: skip
    _assert_answer(env, oracle, "Found 3 row(s).")
    assert not any("beyond LIMIT" in a for a in env["assumptions"]), env["assumptions"]
    # the adversary's repro: grouped by the PARENT table, limit 1. The envelope's own scope check
    # may demote a two-table ranking; what it must never do is state North alone.
    districts = _oracle(
        r.rig.lake,
        "SELECT d.name, SUM(s.budget) AS v FROM bronze.public_schools s "
        "JOIN bronze.public_districts d USING (district_id) GROUP BY d.name",
    )
    env = typed(
        r, "What is the total budget of schools by district?", BUDGET,
        group_by=((DISTRICTS, "name"),), limit=1,
    )  # fmt: skip
    assert env["abstained"] or _multiset(env["rows"]) == _multiset(districts), env["text"]
    assert len(env["rows"]) != 1


def _many_schools(n: int) -> dict[str, Any]:
    held = copy.deepcopy(_lake())
    held["data"]['"public"."schools"'] = (
        ["school_id", "district_id", "name", "budget", "kind", "region"],
        [[i, 1 + i % 2, f"S{i:03d}", Decimal(i), "public", "North"] for i in range(1, n + 1)],
    )
    return held


def test_f4_sixty_groups_are_sixty_rows_not_the_default_fifty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    rigged = build(tmp_path, monkeypatch, minter, store, lake=_many_schools(60))
    env = rigged.plan(
        "What is the total budget of schools by name?",
        BUDGET,
        group_by=((SCHOOLS, "name"),),
    )
    oracle = _oracle(
        rigged.rig.lake, "SELECT name, SUM(budget) AS v FROM bronze.public_schools GROUP BY name"
    )
    assert len(oracle) == 60
    _assert_answer(env, oracle, "Found 60 row(s).")
    assert not any("rows beyond LIMIT" in a for a in env["assumptions"]), env["assumptions"]


def test_f4_more_groups_than_the_lane_states_abstains_instead_of_cutting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    n = mu.CONFIRMED_ROW_CAP + 5
    rigged = build(tmp_path, monkeypatch, minter, store, lake=_many_schools(n))
    env = rigged.plan(
        "What is the total budget of schools by name?",
        BUDGET,
        group_by=((SCHOOLS, "name"),),
    )
    assert env["abstained"] is True and env["rows"] == [] and env["values"] == []
    assert "measure_basis" not in env
    assert "too_many_groups" in env["text"], env["text"]
    assert f"more than {mu.CONFIRMED_ROW_CAP}" in _reasons(env), _reasons(env)


def test_f4_exactly_the_cap_is_whole_and_is_stated_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    rigged = build(tmp_path, monkeypatch, minter, store, lake=_many_schools(mu.CONFIRMED_ROW_CAP))
    env = rigged.plan(
        "What is the total budget of schools by name?",
        BUDGET,
        group_by=((SCHOOLS, "name"),),
    )
    oracle = _oracle(
        rigged.rig.lake, "SELECT name, SUM(budget) AS v FROM bronze.public_schools GROUP BY name"
    )
    assert len(oracle) == mu.CONFIRMED_ROW_CAP
    _assert_answer(env, oracle, f"Found {mu.CONFIRMED_ROW_CAP} row(s).")


# ============================================================ F5: a plan-supplied link choice


def _two_links() -> dict[str, Any]:
    held = copy.deepcopy(_lake())
    held["data"]['"public"."schools"'] = (
        ["school_id", "district_id", "mail_district_id", "name", "budget", "kind", "region"],
        [
            [1, 1, 2, "Alder", Decimal("100.50"), "public", "North"],
            [2, 1, 2, "Birch", Decimal("200.25"), "private", "North"],
            [3, 2, 1, "Cedar", Decimal("50.00"), "public", "South"],
            [4, 1, 1, "Dogwood", Decimal("10.00"), "private", "north"],
        ],
    )
    held["types"]["public.schools"].insert(2, ("mail_district_id", "integer", 32, 0))
    held["fks"] = [
        *held["fks"],
        (
            "schools_mail_district_id_fkey", "public", "schools", "mail_district_id",
            "public", "districts", "district_id", 1,
        ),
    ]  # fmt: skip
    return held


def test_f5_a_plan_supplied_via_does_not_pick_between_two_declared_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    rigged = build(tmp_path, monkeypatch, minter, store, lake=_two_links())
    districts = DISTRICTS
    q = "What is the total budget of schools in North?"
    north = ((districts, "name", "=", "North"),)
    by_district = _oracle(
        rigged.rig.lake,
        "SELECT SUM(budget) AS v FROM bronze.public_schools WHERE district_id = 1",
    )
    by_mail = _oracle(
        rigged.rig.lake,
        "SELECT SUM(budget) AS v FROM bronze.public_schools WHERE mail_district_id = 1",
    )
    assert by_district != by_mail, "the two readings are different numbers"
    # control: with no via the layer itself will not pick
    env = rigged.plan(q, BUDGET, filters=north)
    assert env["abstained"] is True and "ambiguous_path" in env["text"], env["text"]
    for link in ("schools_district_id_fkey", "schools_mail_district_id_fkey"):
        env = typed(rigged, q, BUDGET, filters=north, via={districts: link})
        assert env["abstained"] is True and env["rows"] == [], (link, env["text"], env["rows"])
        assert "ambiguous_path" in env["text"], env["text"]
        assert "measure_basis" not in env and rigged.executed == []


def test_f5_a_via_naming_the_only_declared_link_picks_nothing_and_answers(r: _Rigged) -> None:
    districts = DISTRICTS
    env = typed(
        r, "What is the total budget of schools in North?", BUDGET,
        filters=((districts, "name", "=", "North"),),
        via={districts: "schools_district_id_fkey"},
    )  # fmt: skip
    _assert_answer(env, _sum(r, "district_id = 1"), "310.75")


# ============================================================ F5b: a route the question never chose

ENROLL = "bronze.public_enrollments"
ECOUNT = "enrollments_count"
ENROLL_Q = "How many enrollments are there in North?"
VIA_DIRECT = "enrollments_district_id_fkey"
VIA_SCHOOL = "schools_district_id_fkey"


def _diamond() -> dict[str, Any]:
    """Enrollments reach districts two ways: by their own ``district_id`` (one hop) and by their
    school's district (two hops). Enrollment 5 is North by its own district and South by its
    school; enrollments 1 to 3 are the other way round: direct 1, through the school 3."""
    held = copy.deepcopy(_lake())
    held["tables"] = [*held["tables"], ("public", "enrollments")]
    held["data"]['"public"."enrollments"'] = (
        ["enrollment_id", "school_id", "district_id"],
        [[1, 1, 2], [2, 1, 2], [3, 2, 2], [4, 3, 2], [5, 3, 1]],
    )
    held["types"]["public.enrollments"] = [
        ("enrollment_id", "integer", 32, 0),
        ("school_id", "integer", 32, 0),
        ("district_id", "integer", 32, 0),
    ]
    held["pks"] = [*held["pks"], ("public", "enrollments", "enrollment_id", 1)]
    held["fks"] = [
        *held["fks"],
        (
            "enrollments_school_id_fkey", "public", "enrollments", "school_id",
            "public", "schools", "school_id", 1,
        ),
        (
            VIA_DIRECT, "public", "enrollments", "district_id",
            "public", "districts", "district_id", 1,
        ),
    ]  # fmt: skip
    return held


@pytest.fixture()
def d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> _Rigged:
    return build(tmp_path, monkeypatch, minter, store, lake=_diamond(), also=(ECOUNT,))


def _enrolled(rigged: _Rigged, where: str = "TRUE") -> list[dict[str, Any]]:
    return _oracle(
        rigged.rig.lake, f"SELECT COUNT(*) AS v FROM bronze.public_enrollments WHERE {where}"
    )


def test_f5b_a_via_that_chooses_between_two_routes_is_not_the_customers_choice(
    d: _Rigged,
) -> None:
    """The adversary's repro. "In North" is the enrollment's own district (1) or its school's
    district (3): both are valid joins, each is one green number, neither is what was asked."""
    direct = _enrolled(d, "district_id = 1")
    through = _enrolled(
        d, "school_id IN (SELECT school_id FROM bronze.public_schools WHERE district_id = 1)"
    )
    assert (direct, through) == ([{"v": 1}], [{"v": 3}]), "the two readings are different numbers"
    north = ((DISTRICTS, "name", "=", "North"),)
    for via in (VIA_DIRECT, VIA_SCHOOL):
        env = typed(d, ENROLL_Q, ECOUNT, filters=north, via={DISTRICTS: via})
        assert env["abstained"] is True and env["rows"] == [], (via, env["text"], env["rows"])
        assert "ambiguous_path" in env["text"], env["text"]
        assert VIA_DIRECT in env["text"] and VIA_SCHOOL in env["text"], "both routes are named"
        assert "measure_basis" not in env and "Found 1 row" not in env["text"]
        assert d.executed == [] and d.rig.cortex.asks == []


def test_f5b_the_layers_own_shortest_path_does_not_choose_either(d: _Rigged) -> None:
    """With no via the compiler takes the shorter route and says nothing; the confirmed lane
    states one figure under a confirmed basis, so it does not state that one."""
    north = ((DISTRICTS, "name", "=", "North"),)
    for env in (
        d.plan(ENROLL_Q, ECOUNT, filters=north),
        typed(d, ENROLL_Q, ECOUNT, filters=north),
    ):
        assert env["abstained"] is True and env["rows"] == [], env["text"]
        assert "ambiguous_path" in env["text"], env["text"]
        assert "measure_basis" not in env and "Found 1 row" not in env["text"]
    assert d.executed == []


def test_f5b_a_breakdown_has_the_same_two_readings(d: _Rigged) -> None:
    env = d.plan(
        "How many enrollments are there by districts?",
        ECOUNT,
        group_by=((DISTRICTS, "name"),),
    )
    assert env["abstained"] is True and env["rows"] == [], env["text"]
    assert "ambiguous_path" in env["text"], env["text"]
    assert "measure_basis" not in env and d.executed == []


def test_f5b_controls_a_single_route_still_answers_next_to_the_diamond(d: _Rigged) -> None:
    """The enrollments table has one route to schools, and the schools measure has one route to
    districts: neither is ambiguous, and neither is refused."""
    _assert_answer(d.plan("How many enrollments are there?", ECOUNT), _enrolled(d), "5")
    env = d.plan(
        "How many enrollments are there in Alder?",
        ECOUNT,
        filters=((SCHOOLS, "name", "=", "Alder"),),
    )
    _assert_answer(env, _enrolled(d, "school_id = 1"), "2")
    env = d.plan(
        "What is the total budget of schools in North?",
        BUDGET,
        filters=((DISTRICTS, "name", "=", "North"),),
    )
    _assert_answer(env, _sum(d, "district_id = 1"), "310.75")
    # a via that names the only route to its object picks nothing
    env = typed(
        d, "What is the total budget of schools in North?", BUDGET,
        filters=((DISTRICTS, "name", "=", "North"),),
        via={DISTRICTS: VIA_SCHOOL},
    )  # fmt: skip
    _assert_answer(env, _sum(d, "district_id = 1"), "310.75")


def test_f5b_unit_the_routes_are_counted_per_object_the_plan_uses() -> None:
    onto = _routes_onto()

    def refusal(
        *, via: dict[str, str] | None = None, group_by: Any = (), filters: Any = ()
    ) -> str | None:
        return mu.route_choice_reason(
            onto, grain="fact", via=via, group_by=group_by, filters=filters
        )

    on_dim = [("dim", "c", "=", "x")]
    on_mid = [("mid", "c", "=", "x")]
    assert refusal(filters=on_dim)
    assert refusal(group_by=[("dim", "c")])
    assert refusal(via={"dim": "mid_dim"}, filters=on_dim)
    assert refusal(via={"dim": "fact_dim"}, filters=on_dim)
    # one route each: no refusal, with or without the via that names it
    assert refusal(filters=on_mid) is None
    assert refusal(via={"mid": "fact_mid"}, filters=on_mid) is None
    assert refusal(group_by=[("fact", "c")]) is None
    # the reason names the object, the count and the routes
    why = refusal(filters=on_dim)
    assert why is not None and why.startswith("ambiguous_path:")
    assert "'dim'" in why and "more than one route" in why
    assert "fact_dim" in why and "fact_mid -> mid_dim" in why


def test_f5b_mutation_without_the_gate_each_reading_is_stated_as_if_it_were_the_answer(
    d: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harm the gate prevents, shown by turning it off: the same question answers 1 (direct)
    with no via and with the direct via, and 3 (through the school) with the other, each as a
    confirmed figure with no sign of the other reading."""
    import dms_executor.generative_ask as ga

    monkeypatch.setattr(ga, "route_choice_reason", lambda *a, **k: None)
    north = ((DISTRICTS, "name", "=", "North"),)
    _assert_answer(d.plan(ENROLL_Q, ECOUNT, filters=north), _enrolled(d, "district_id = 1"), "1")
    env = typed(d, ENROLL_Q, ECOUNT, filters=north, via={DISTRICTS: VIA_DIRECT})
    _assert_answer(env, [{"v": 1}], "1")
    env = typed(d, ENROLL_Q, ECOUNT, filters=north, via={DISTRICTS: VIA_SCHOOL})
    _assert_answer(env, [{"v": 3}], "3")


def test_f5b_unit_a_via_naming_the_grain_picks_the_parents_reading_over_the_rows_own() -> None:
    onto = _verified(("emp",), (("emp_manager", "emp", "emp"),))
    kw: dict[str, Any] = {"grain": "emp", "group_by": [("emp", "name")], "filters": ()}
    assert mu.route_choice_reason(onto, via=None, **kw) is None, "the row's own attribute"
    why = mu.route_choice_reason(onto, via={"emp": "emp_manager"}, **kw)
    assert why is not None and why.startswith("ambiguous_path:") and "itself" in why


def test_f5b_unit_a_route_count_the_search_cannot_finish_is_not_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onto = _routes_onto()
    assert onto.routes_between("fact", "dim", for_filter=True, budget=1) is None
    monkeypatch.setattr(type(onto), "routes_between", lambda self, *a, **k: None)
    why = mu.route_choice_reason(
        onto, grain="fact", via=None, group_by=(), filters=[("mid", "c", "=", "x")]
    )
    assert why is not None and why.startswith("ambiguous_path:") and "too many" in why


def test_f5b_unit_counting_routes_stops_at_two_however_tangled_the_graph() -> None:
    """Thirty layers of three tables, every table linked to every table of the next layer: 3**29
    routes to the last one. The question is whether there is more than one, and two answer it."""
    layers = [[f"t{layer}_{n}" for n in range(3)] for layer in range(30)]
    names = tuple(name for layer in layers for name in layer)
    links = tuple(
        (f"{a}__{b}", a, b)
        for upper, lower in zip(layers, layers[1:], strict=False)
        for a in upper
        for b in lower
    )
    onto = _verified(names, links)
    started = time.monotonic()
    routes = onto.routes_between(layers[0][0], layers[-1][0], for_filter=True)
    assert routes is not None and len(routes) == 2
    assert time.monotonic() - started < 2.0
    one = _verified(("a", "b"), (("a_b", "a", "b"),))
    assert [len(r) for r in one.routes_between("a", "b") or []] == [1]
    assert one.routes_between("b", "a") == [], "links run from child to parent only"
    assert one.routes_between("a", "a") == [[]]


# ============================================================ F7: one value, two spellings


def test_f7_a_value_the_column_holds_under_two_spellings_is_not_filtered_to_one_of_them(
    r: _Rigged,
) -> None:
    """region holds 'North' twice and 'north' once. The equality matches ONE spelling and drops
    the other rows without a trace: 10.00 for 'north', 300.75 for 'North', 310.75 for either."""
    assert _sum(r, "region = 'north'") == [{"v": Decimal("10.00")}]
    assert _sum(r, "lower(region) = 'north'") == [{"v": Decimal("310.75")}]
    for literal in ("north", "North"):
        env = r.plan(
            f"What is the total budget of schools in region {literal}?",
            BUDGET,
            filters=((SCHOOLS, "region", "=", literal),),
        )
        text = _assert_abstain(env, "ambiguous_filter_value")
        assert "more than one spelling" in text and literal not in text.replace("North", "")
        assert "measure_basis" not in env and r.executed == []
        assert "10.0" not in text and "300.75" not in text
    # a value with one spelling still answers, and IN is probed value by value
    env = r.plan(
        "What is the total budget of schools in region South?",
        BUDGET,
        filters=((SCHOOLS, "region", "=", "South"),),
    )
    _assert_answer(env, _sum(r, "region = 'South'"), "50")
    env = r.plan(
        "What is the total budget of schools in region North or South?",
        BUDGET,
        filters=((SCHOOLS, "region", "IN", ["North", "South"]),),
    )
    assert env["abstained"] is True and "measure_basis" not in env


# ============================================================ F8: a request for the rows


@pytest.mark.parametrize(
    "question", ["Show me the schools", "What are the schools?", "Find the schools", "List schools"]
)
def test_f8_a_request_for_the_rows_is_not_answered_with_a_count(r: _Rigged, question: str) -> None:
    for env in (r.plan(question, SCOUNT), ranked(r, question, SCOUNT)):
        assert env["abstained"] is True and env["rows"] == [], (question, env["text"])
        assert "measure_basis" not in env and "schools_count=4" not in str(env["text"])
    assert r.executed == []


def test_f8_a_question_that_asks_for_the_count_still_answers(r: _Rigged) -> None:
    oracle = _oracle(r.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")
    for question in (
        "How many schools are there?",
        "What is the number of schools?",
        "Count the schools",
        "What is the total number of schools?",
    ):
        _assert_answer(r.plan(question, SCOUNT), oracle, "4")


def test_f8_unit_only_a_count_needs_a_cue_to_be_asked_for() -> None:
    count = {SCOUNT: _measure(SCOUNT, SCHOOLS, aggregate="count", column="*")}
    for question in ("Show me the schools", "What are the schools?"):
        got = mu.measure_fit(question, count, filters=[], tables={SCHOOLS: "schools"})
        assert (got.ok, got.reason) == (False, "aggregate_cue"), question
    assert mu.measure_fit("How many schools?", count, filters=[], tables={SCHOOLS: "schools"}).ok
    # a sum without a cue is a plain "the budget of the schools": unchanged
    total = {BUDGET: _measure(BUDGET, SCHOOLS)}
    q = "What is the budget of the schools?"
    assert mu.measure_fit(q, total, filters=[], tables={SCHOOLS: "schools"}).ok


# ------------------------------------------------------------------------ unit helpers


def _measure(name: str, grain: str, *, aggregate: str = "sum", column: str = "budget") -> Measure:
    prov = MeasureProvenance(
        measure_id="m1",
        name=name,
        grain=grain,
        aggregate=aggregate,
        column=column,
        column_type="DECIMAL(10,2)",
        definition_hash="h" * 64,
        version_id="v1",
        decided_at="2026-10-15T00:00:00",
        ledger_entry_id="led_1",
        persisted=False,
        columns_checked=True,
    )
    return Measure(name, grain, f"{aggregate.upper()}({column})", True, "", provenance=prov)


def _onto_with(measure: Measure) -> Any:
    from dms_executor.ontology import Ontology

    onto = Ontology()
    onto.add_object(measure.grain, measure.grain, ["school_id"])
    onto.measures[measure.name] = measure
    return onto


def _routes_onto() -> Any:
    """fact -> dim directly and fact -> mid -> dim: two routes to dim, one to mid."""
    return _verified(
        ("fact", "mid", "dim"),
        (("fact_dim", "fact", "dim"), ("fact_mid", "fact", "mid"), ("mid_dim", "mid", "dim")),
    )


def _verified(names: tuple[str, ...], links: tuple[tuple[str, str, str], ...]) -> Any:
    """An ontology of ``names`` with many-to-one ``(name, from, to)`` links, as verify() leaves
    it."""
    from dataclasses import replace

    from dms_executor.ontology import Ontology

    onto = Ontology()
    for name in names:
        onto.add_object(name, f"bronze.{name}", ["id"])
    for name, src, dst in links:
        onto.add_link(name, src, ["fk"], dst, ["id"])
    for name, link in list(onto.links.items()):
        onto.links[name] = replace(link, cardinality="many_to_one")
    return onto


# ============================================================ round 0: the question's own words,
# and the column a stated value is bound to


def test_r0_a_filter_value_the_question_uses_as_its_own_word_is_not_stated(r: _Rigged) -> None:
    """Adversary round 0 (confirmed lane, P0). A dirty subtotal row labelled ``all`` matches the
    plan's filter ``region = 'all'``. The question says "all schools" and names no region: "all"
    is its frame word. The lane claimed the frame word as the value, so the sum over the dirty
    row (50.0) was stated as the answer under the confirmed badge. Refused by name."""
    _lake_exec(r.rig.lake, "UPDATE bronze.public_schools SET region = 'all' WHERE school_id = 3")
    env = r.plan(TOTAL_Q, BUDGET, filters=((SCHOOLS, "region", "=", "all"),))
    _refused(r, env, "unstated_filter")
    # a value that is not one of the question's words is still a value the question can state
    _lake_exec(
        r.rig.lake, "UPDATE bronze.public_schools SET region = 'Hilltop' WHERE school_id = 3"
    )
    env = r.plan(
        "What is the total budget of schools in Hilltop?",
        BUDGET,
        filters=((SCHOOLS, "region", "=", "Hilltop"),),
    )
    _assert_answer(env, [{BUDGET: Decimal("50.00")}], "50")


def test_r0_a_value_the_plan_binds_to_a_column_the_question_does_not_name_is_ambiguous(
    r: _Rigged,
) -> None:
    """Adversary round 0 (confirmed lane, P0). 'North' is a region and, here, also a kind: the
    plan puts it on ``kind`` and the question says "in North", which names no column. The
    filter matched one school and the total (100.50) was stated as the schools in North (300.75
    by region). Refused as ambiguous, and the same question naming the column still answers."""
    _lake_exec(r.rig.lake, "UPDATE bronze.public_schools SET kind = 'North' WHERE school_id = 1")
    env = r.plan(
        "What is the total budget of schools in North?",
        BUDGET,
        filters=((SCHOOLS, "kind", "=", "North"),),
    )
    _assert_abstain(env, "ambiguous_filter_value")
    assert r.rig.cortex.asks == [] and r.executed == []
    # the same value, the same column, named beside the value: the figure is that column's
    env = r.plan(
        "What is the total budget of schools in kind North?",
        BUDGET,
        filters=((SCHOOLS, "kind", "=", "North"),),
    )
    _assert_answer(env, [{BUDGET: Decimal("100.50")}], "100.5")


def test_r0_a_number_stated_after_the_table_word_is_not_a_district_id(r: _Rigged) -> None:
    """Adversary round 0 (confirmed lane, P0). "school 1" names a school; the plan filters
    ``district_id = 1``, the whole district (310.75), not school 1 (100.50). The word "school"
    is the table's name, shared by every column of it, so it binds no column. Refused."""
    env = r.plan(
        "What is the total budget of school 1?",
        BUDGET,
        filters=((SCHOOLS, "district_id", "=", 1),),
    )
    _refused(r, env, "unbound_constraint")
    # the number after the word of its own column is the district the question names
    env = r.plan(
        "What is the total budget of schools in district 1?",
        BUDGET,
        filters=((SCHOOLS, "district_id", "=", 1),),
    )
    _assert_answer(env, [{BUDGET: Decimal("310.75")}], "310.75")
