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
  question made.

Non-confirmed lanes are untouched; this file only reads the confirmed lane.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import test_ingest_fixes_277 as ing
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import db_connector as dbc
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
) -> _Rigged:
    held = lake if lake is not None else _lake(schema)
    monkeypatch.setattr(ing, "PKS", held["pks"])
    monkeypatch.setattr(ing, "FKS", held["fks"])
    rig = ing._Rig(tmp_path, monkeypatch, minter)
    space = rig.space("guards")
    rig.ingest(space, tables=held["tables"], data=held["data"], types=held["types"])
    env = _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())
    rigged = _Rigged(env)
    rigged.confirm(BUDGET, SCOUNT)
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
        "How many schools are there with id 1 and 2?",
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
        "What is the total budget of schools in North in region North?", BUDGET, filters=both
    )
    oracle = _sum(r, "district_id = 1 AND region = 'North'")
    assert oracle == [{"v": Decimal("310.75")}]
    _assert_answer(env, oracle, "310.75")


def test_f2_unit_the_accounting_counts_multiplicity() -> None:
    acc = mu.account_for_question
    q = "total budget of schools in North"
    two = [("o", "a", "=", "North"), ("o", "b", "=", "North")]
    assert acc(q, measure=BUDGET, grain="bronze.public_schools", filters=two).reason == (
        "unstated_filter"
    )
    q2 = "total budget of schools in North North"
    assert acc(q2, measure=BUDGET, grain="bronze.public_schools", filters=two).ok
    # one IN list never repeats a value
    assert acc(
        q,
        measure=BUDGET,
        grain="bronze.public_schools",
        filters=[("o", "a", "IN", ["North", "North"])],
    ).ok


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
