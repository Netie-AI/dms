"""Ask-time use of confirmed measures (dms#283 / #284, spec step 6): units.

Pure units (``aggregate_cues``, ``measure_fit``, the wire budget, the demo-alias rule) and the
load/prune/probe functions against a real ingested Space on a real DuckDB lake. Nothing here
is on the answer path by itself; ``tests/test_connect_ask_02.py`` asserts the same behaviour at
the customer envelope (``POST /v1/chat/ask``). Every gate below has a test that fails when the
gate is switched off (the mutation record is in the commit message).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import duckdb
import pytest
from dms_core.control_plane.onto_store import (
    OntologyStore,
    SourceIdentity,
    measure_definition_hash,
)
from dms_executor import generative_ask as ga
from dms_executor import measure_use as mu
from dms_executor.measure_use import (
    FIT_REASONS,
    FitVerdict,
    aggregate_cues,
    attach_confirmed_measures,
    confirmed_measure_suggestions,
    filter_values_present,
    measure_exclusions,
    prune_unusable_measures,
)
from dms_executor.measure_use import measure_fit as _measure_fit
from dms_executor.ontology import Measure, MeasureProvenance, Ontology, measure_expression
from dms_executor.space_ontology import (
    WIRE_MAX_BYTES,
    WIRE_MAX_MEASURE_BYTES,
    budget_space_block,
    load_space_ontology,
    set_ontology_store,
    space_catalog,
)
from test_ingest_fixes_277 import _Rig
from test_onto_confirm_01 import (
    ACTOR,
    DISTRICTS,
    SCHOOLS,
    _Env,
    _lake_exec,
    _Ledger,
    _snapshot_with,
)
from test_onto_derive_01 import make_minter

EXECUTOR_DIR = Path(__file__).resolve().parents[1] / "packages" / "executor" / "dms_executor"
ALLOWED = {SCHOOLS, DISTRICTS}
#: What ``read_source_pulls`` names for the rig's relations: the table at the source, schema
#: (``public``) left off. The fit reads a relation's words from here and never from its landed
#: name, so a unit call that does not pass it contributes no relation word at all.
SOURCE_TABLES = {SCHOOLS: "schools", DISTRICTS: "districts"}


def measure_fit(question: str, measures: Any, **kw: Any) -> FitVerdict:
    """``measure_fit`` as the ask path calls it: with the plan's relations named by their source
    table. A test that wants none passes ``tables={}``."""
    kw.setdefault("tables", SOURCE_TABLES)
    return _measure_fit(question, measures, **kw)


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


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore) -> _Env:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Measure use")
    rig.ingest(space)
    return _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())


# ------------------------------------------------------------------ aggregate cues


@pytest.mark.parametrize(
    ("question", "cues"),
    [
        ("What is the total budget of all schools?", {"sum"}),
        ("What is the average budget of schools?", {"avg"}),
        ("mean school budget", {"avg"}),
        ("What is the avg budget", {"avg"}),
        ("How many schools are there?", {"count"}),
        ("What is the number of schools", {"count"}),
        ("count the schools", {"count"}),
        ("a tally of schools", {"count"}),
        ("total number of students", {"sum", "count"}),
        ("Which school has the highest budget?", {"max"}),
        ("the largest, greatest and biggest budget", {"max"}),
        ("the lowest budget", {"min"}),
        ("smallest or least budget", {"min"}),
        ("minimum and maximum budget", {"min", "max"}),
        ("How many distinct districts", {"count", "distinct"}),
        ("unique districts", {"distinct"}),
        ("overall budget", {"sum"}),
        ("budget altogether", {"sum"}),
        ("combined budget", {"sum"}),
        ("Totals by district", {"sum"}),  # plural stems to the cue
        ("Counts per district", {"count"}),
        ("tallies per district", {"count"}),
        ("list the schools", set()),
        ("summary of schools", set()),  # a whole word, not a prefix of "sum"
        ("how are many schools", set()),  # a phrase is consecutive words
        ("number is large", set()),
        ("", set()),
    ],
)
def test_aggregate_cues_table(question: str, cues: set[str]) -> None:
    assert aggregate_cues(question) == frozenset(cues)


def test_aggregate_cues_are_case_and_punctuation_blind() -> None:
    assert aggregate_cues("TOTAL, budget!") == aggregate_cues("total budget") == {"sum"}
    assert aggregate_cues("HOW   MANY?") == {"count"}


# ------------------------------------------------------------------ measure_fit


def _m(name: str, aggregate: str = "sum", column: str = "budget", grain: str = SCHOOLS) -> Measure:
    return Measure(
        name,
        grain,
        measure_expression(aggregate, column),
        provenance=MeasureProvenance(
            measure_id=f"id-{name}",
            name=name,
            grain=grain,
            aggregate=aggregate,
            column=column,
            column_type="DECIMAL(10,2)",
            definition_hash="0" * 64,
            version_id="v1",
            decided_at="2026-10-09T00:00:00+00:00",
            ledger_entry_id="led_1",
            persisted=False,
        ),
    )


def _pool(*measures: Measure) -> dict[str, Measure]:
    return {m.name: m for m in measures}


BUDGET = _m("schools_budget_sum")
SCOUNT = _m("schools_count", "count", "*")
DCOUNT = _m("districts_count", "count", "*", DISTRICTS)
STUDENTS = _m("students_sum", "sum", "students")


def test_fit_picks_the_measure_the_question_names_and_prefers_the_more_specific() -> None:
    pool = _pool(BUDGET, SCOUNT, DCOUNT)
    got = measure_fit("What is the total budget of all schools?", pool)
    assert got == FitVerdict(
        True, "schools_budget_sum", "ok"
    )  # 2 content tokens beat schools_count's 1
    assert measure_fit("How many schools are there?", pool) == FitVerdict(
        True, "schools_count", "ok"
    )
    assert measure_fit("How many districts are there?", pool) == FitVerdict(
        True, "districts_count", "ok"
    )


def test_fit_candidate_must_equal_dms_own_pick() -> None:
    pool = _pool(BUDGET, SCOUNT, DCOUNT)
    q = "What is the total budget of all schools?"
    assert measure_fit(q, pool, candidate="schools_budget_sum").ok is True
    got = measure_fit(q, pool, candidate="districts_count")
    assert got == FitVerdict(False, "schools_budget_sum", "ranker_disagrees")
    # a candidate that is not a structured, loaded measure is not a match at all
    assert measure_fit(q, pool, candidate="nope") == FitVerdict(False, None, "no_match")
    # the ranker's pick being less specific than DMS's is a disagreement too
    assert measure_fit(q, pool, candidate="schools_count").reason == "ranker_disagrees"


@pytest.mark.parametrize(
    ("question", "measure", "reason"),
    [
        # average vs SUM
        ("What is the average budget of schools?", BUDGET, "aggregate_cue"),
        ("What is the mean budget of schools?", BUDGET, "aggregate_cue"),
        # how many vs SUM
        ("How many students are there?", STUDENTS, "aggregate_cue"),
        ("What is the number of students?", STUDENTS, "aggregate_cue"),
        # highest / lowest vs SUM
        ("Which school has the highest budget?", BUDGET, "aggregate_cue"),
        ("What is the lowest budget of schools?", BUDGET, "aggregate_cue"),
        # a second cue does not launder a conflicting first one
        ("What is the average total budget of schools?", BUDGET, "aggregate_cue"),
        ("What is the total average budget of schools?", BUDGET, "aggregate_cue"),
        ("What is the total highest budget of schools?", BUDGET, "aggregate_cue"),
        ("What is the overall mean budget of schools?", BUDGET, "aggregate_cue"),
        ("What is the budget of schools, total and average?", BUDGET, "aggregate_cue"),
        # count phrasing fits a count and NOTHING else, a SUM included
        ("What is the total number of students?", STUDENTS, "aggregate_cue"),
        ("What is the total count of students?", STUDENTS, "aggregate_cue"),
        (
            "What is the total number of students?",
            _m("students_avg", "avg", "students"),
            "aggregate_cue",
        ),
        # total vs COUNT, average vs COUNT
        ("What is the total of schools?", SCOUNT, "aggregate_cue"),
        ("What is the average of schools?", SCOUNT, "aggregate_cue"),
    ],
)
def test_fit_aggregate_cue_conflict_is_not_ok(question: str, measure: Measure, reason: str) -> None:
    got = measure_fit(question, _pool(measure))
    assert got.ok is False and got.reason == reason and got.pick == measure.name


@pytest.mark.parametrize(
    ("question", "measure"),
    [
        ("What is the total budget of schools?", BUDGET),
        ("What is the budget of schools?", BUDGET),  # no cue at all: nothing conflicts
        # "total number" / "total count" are count phrasing: they fit COUNT and COUNT DISTINCT
        ("What is the total number of students?", _m("students_count", "count", "students")),
        ("What is the total count of students?", _m("students_count", "count", "students")),
        (
            "What is the total number of students?",
            _m("students_distinct", "count_distinct", "id", SCHOOLS),
        ),
        ("How many schools are there?", SCOUNT),
        ("What is the average budget of schools?", _m("schools_budget_avg", "avg")),
        ("What is the highest budget of schools?", _m("schools_budget_max", "max")),
        ("What is the lowest budget of schools?", _m("schools_budget_min", "min")),
        (
            "How many distinct districts?",
            _m("districts_distinct", "count_distinct", "name", DISTRICTS),
        ),
        ("How many districts?", _m("districts_distinct", "count_distinct", "name", DISTRICTS)),
    ],
)
def test_fit_compatible_cues_are_ok(question: str, measure: Measure) -> None:
    assert measure_fit(question, _pool(measure)) == FitVerdict(True, measure.name, "ok")


def test_fit_a_distinct_question_is_not_answered_by_a_plain_count_or_sum() -> None:
    q = "How many distinct districts are there?"
    assert measure_fit(q, _pool(DCOUNT)).reason == "aggregate_cue"  # COUNT is not DISTINCT
    sums = _m("districts_sum", "sum", "name", DISTRICTS)
    assert measure_fit("total unique districts", _pool(sums)).reason == "aggregate_cue"
    # and COUNT DISTINCT is not answered by a "total" question
    distinct = _m("districts_distinct", "count_distinct", "name", DISTRICTS)
    assert measure_fit("total districts", _pool(distinct)).reason == "aggregate_cue"


def test_fit_ties_are_ambiguous_and_name_no_pick() -> None:
    a = _m("students_enrollment_sum", "sum", "enrollment")
    b = _m("students_capacity_sum", "sum", "capacity")
    q = "total students enrollment capacity"
    assert measure_fit(q, _pool(a, b)) == FitVerdict(False, None, "ambiguous")
    assert measure_fit(q, _pool(b, a)) == FitVerdict(False, None, "ambiguous")  # order-blind
    # SUM and AVG of one column carry the same words: a tie, never a coin flip
    s, avg = _m("schools_budget_sum"), _m("schools_budget_avg", "avg")
    assert measure_fit("average budget of schools", _pool(s, avg)).reason == "ambiguous"
    # one more word on one side breaks the tie
    assert (
        measure_fit("total enrollment of students", _pool(a, b)).pick == "students_enrollment_sum"
    )


@pytest.mark.parametrize(
    "question",
    [
        "What is the budget share of schools?",
        "What is the budget growth of schools?",
        "What is the budget ratio of schools?",
        "What is the budget rate of schools?",
        "What is the budget percentage of schools?",
        "What is the budget change of schools?",
        "How does the budget of schools trend?",
        "budget of schools vs districts",
        "What is the budget of schools versus districts",
        "What is the budget margin of schools?",
        "What % of the budget do schools hold?",
    ],
)
def test_fit_quantity_veto(question: str) -> None:
    got = measure_fit(question, _pool(BUDGET))
    assert got == FitVerdict(False, "schools_budget_sum", "quantity_cue"), question


def test_fit_quantity_word_is_fine_when_the_measure_name_says_so() -> None:
    rate = _m("attendance_rate_avg", "avg", "rate")
    assert measure_fit("What is the average attendance rate?", _pool(rate)).ok is True
    assert measure_fit("What is the attendance rate share?", _pool(rate)).reason == "quantity_cue"
    pct = _m("pass_pct_avg", "avg", "pct")
    assert measure_fit("average pass pct", _pool(pct)).ok is True
    assert measure_fit("average pass percent", _pool(pct)).reason == "no_match"  # token must match


def test_fit_measure_with_no_content_tokens_cannot_be_picked_or_named() -> None:
    vague = _m("total_count", "count", "*")  # every word is an aggregate word
    assert measure_fit("total count", _pool(vague), candidate="total_count") == FitVerdict(
        False, None, "no_content_tokens"
    )
    assert measure_fit("total count", _pool(vague)) == FitVerdict(False, None, "no_match")
    # alongside a real measure it never competes
    assert measure_fit("total count of schools", _pool(vague, SCOUNT)).pick == "schools_count"


def test_fit_no_match_and_unstructured_measures() -> None:
    assert measure_fit("What is the weather?", _pool(BUDGET, SCOUNT)) == FitVerdict(
        False, None, "no_match"
    )
    assert measure_fit("anything", {}) == FitVerdict(False, None, "no_match")
    plain = Measure("schools_budget_sum", SCHOOLS, "SUM(budget)")  # no provenance: not confirmed
    assert (
        measure_fit("total budget of schools", {"schools_budget_sum": plain}).reason == "no_match"
    )
    assert measure_fit(
        "total budget of schools", {"schools_budget_sum": plain}, candidate="schools_budget_sum"
    ) == FitVerdict(False, None, "no_match")


def test_fit_stems_plurals_the_same_way_on_both_sides() -> None:
    country = _m("country_count", "count", "*")
    assert measure_fit("How many countries are there?", _pool(country)).ok is True
    assert measure_fit("How many country are there?", _pool(country)).ok is True
    cats = _m("categories_count", "count", "*")
    assert measure_fit("How many category are there?", _pool(cats)).ok is True


def test_fit_reasons_are_the_documented_vocabulary() -> None:
    assert FIT_REASONS == {
        "ok", "ambiguous", "no_match", "aggregate_cue", "quantity_cue", "qualifier_cue",
        "unbound_constraint", "unstated_filter", "no_content_tokens", "ranker_disagrees",
        "question_too_long",
    }  # fmt: skip


# ------------------------------------------------------------------ load: attach_confirmed_measures


def _confirm_named(env: _Env, name: str) -> dict[str, Any]:
    """Propose (once) and confirm one of the proposer's measures through the real service."""
    if not env.store.measures_for_space(env.sid):
        env.propose()
    views = {m["name"]: m for m in env.view()["measures"]}
    return dict(env.confirm(views[name])["measure"])


def test_load_with_nothing_confirmed_is_unchanged(env: _Env) -> None:
    env.propose()  # proposals exist; none is confirmed
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None and onto.measures == {}
    assert measure_exclusions(onto) == {}
    assert set(onto.objects) == {SCHOOLS, DISTRICTS}


def test_a_confirmed_row_loads_with_provenance(env: _Env) -> None:
    confirmed = _confirm_named(env, "schools_budget_sum")
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    assert set(onto.measures) == {"schools_budget_sum"}
    m = onto.measures["schools_budget_sum"]
    row = env.row(confirmed["id"])
    assert m.grain == SCHOOLS and m.description == row.description
    assert m.expression == measure_expression("sum", "budget")
    prov = m.provenance
    assert prov is not None
    assert prov == MeasureProvenance(
        measure_id=str(row.id),
        name="schools_budget_sum",
        grain=SCHOOLS,
        aggregate="sum",
        column="budget",
        column_type="DECIMAL(10,2)",
        definition_hash=row.definition_hash,
        version_id=str(row.version_id),
        decided_at=row.decided_at.isoformat() if row.decided_at else "",
        ledger_entry_id=row.ledger_entry_id or "",
        persisted=False,  # the in-memory store says so, and the ask will say so
        columns_checked=False,  # not yet checked against the lake: prune does that
    )
    assert prov.ledger_entry_id == "led_m1" and prov.decided_at
    assert m.additive is True
    assert measure_exclusions(onto) == {}


def test_a_count_measure_loads_with_the_star_column(env: _Env) -> None:
    _confirm_named(env, "schools_count")
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    m = onto.measures["schools_count"]
    assert m.expression == "COUNT(*)" and m.provenance is not None
    assert m.provenance.column == "*" and m.provenance.column_type == ""


def test_proposed_and_rejected_rows_never_load(env: _Env) -> None:
    views = env.proposed()  # schools_budget_sum, schools_count, districts_count: all proposed
    env.confirm(views["schools_count"])
    env.reject(views["districts_count"], "not needed")
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    assert set(onto.measures) == {"schools_count"}
    assert "schools_budget_sum" not in onto.measures  # proposed
    assert "districts_count" not in onto.measures  # rejected
    # they were never read, so they are not "excluded rows" either: a plan naming one gets the
    # plain "not a measure this Space declares"
    assert measure_exclusions(onto) == {}
    # a withdrawn (confirmed -> rejected) measure leaves the very next load
    env.reject(env.row(views["schools_count"]["id"]), "withdrawn")
    again = load_space_ontology(env.space, env.store)
    assert again is not None and again.measures == {}


def test_a_confirmed_row_of_a_non_active_version_never_loads(env: _Env) -> None:
    v1 = env.version()
    drifted = env.store.reconnect(space_id=env.sid, identity=v1.identity, fingerprint="changed")
    assert drifted.status == "proposed"
    env.store.record_snapshot(
        drifted.id, body=dict(env.snapshot(v1).body), violations=[], verified=True
    )
    row = env.raw("drifted_count", version=drifted)
    env.store.decide_measure(
        row.id,
        to_state="confirmed",
        expected_hash=row.definition_hash,
        evidence={"column_type": ""},
        actor=ACTOR,
        ledger=lambda _payload: "led_drift",
    )
    assert env.row(row.id).state == "confirmed"
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None and "drifted_count" not in onto.measures


def test_a_hash_mismatch_is_skipped_as_integrity_and_recorded(env: _Env) -> None:
    confirmed = _confirm_named(env, "schools_budget_sum")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    # any edit that does not recompute the hash: the column, the description, the aggregate
    for edit in ({"column_name": "name"}, {"description": "Edited."}, {"aggregate": "avg"}):
        original = env.store._measures[idx]
        env.store._measures[idx] = replace(original, **edit)
        onto = load_space_ontology(env.space, env.store)
        assert onto is not None
        assert "schools_budget_sum" not in onto.measures, edit
        assert measure_exclusions(onto) == {"schools_budget_sum": "integrity"}, edit
        env.store._measures[idx] = original
    restored = load_space_ontology(env.space, env.store)
    assert restored is not None and "schools_budget_sum" in restored.measures


def test_a_confirmed_row_without_its_steward_time_or_ledger_pointer_has_no_authority(
    env: _Env,
) -> None:
    confirmed = _confirm_named(env, "schools_count")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    original = env.store._measures[idx]
    for edit in ({"ledger_entry_id": None}, {"ledger_entry_id": " "}, {"decided_by": None},
                 {"decided_at": None}):  # fmt: skip
        env.store._measures[idx] = replace(original, **edit)
        onto = load_space_ontology(env.space, env.store)
        assert onto is not None and onto.measures == {}, edit
        assert measure_exclusions(onto) == {"schools_count": "integrity"}, edit


def test_a_hostile_column_with_a_recomputed_hash_loads_as_a_quoted_identifier_only(
    env: _Env,
) -> None:
    """The hash cannot catch a writer who recomputes it; the SQL text is rebuilt from the spec
    through the one construction site, so the string is one quoted identifier, never SQL."""
    confirmed = _confirm_named(env, "schools_budget_sum")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    hostile = 'x"); DROP TABLE bronze.public_schools; --'
    row = env.store._measures[idx]
    env.store._measures[idx] = replace(
        row,
        column_name=hostile,
        definition_hash=measure_definition_hash(
            row.name, row.grain, row.aggregate, hostile, row.description
        ),
    )
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    m = onto.measures["schools_budget_sum"]
    assert m.expression == measure_expression("sum", hostile)
    assert m.expression == 'SUM(f."x""); DROP TABLE bronze.public_schools; --")'


def test_an_orphan_row_is_skipped_and_never_raises_out_of_load(env: _Env) -> None:
    """The grain object is no longer in the Space's ontology: ``add_measure`` would raise
    KeyError and turn the whole Space into ``ontology_store_unavailable``."""
    views = env.proposed()
    env.confirm(views["schools_budget_sum"])
    env.confirm(views["districts_count"])

    def drop_schools(body: dict[str, Any]) -> None:
        cat = dict(body["catalog"])
        cat["tables"] = [t for t in cat["tables"] if t["path"] != SCHOOLS]
        cat["primary_keys"] = {k: v for k, v in cat["primary_keys"].items() if k != SCHOOLS}
        cat["foreign_keys"] = [
            f for f in cat["foreign_keys"] if SCHOOLS not in (f["from_table"], f["to_table"])
        ]
        body["catalog"] = cat

    _snapshot_with(env, drop_schools)
    onto = load_space_ontology(env.space, env.store)  # must not raise
    assert onto is not None and set(onto.objects) == {DISTRICTS}
    assert set(onto.measures) == {"districts_count"}  # the healthy one is untouched
    assert measure_exclusions(onto) == {"schools_budget_sum": "grain_missing"}


def test_two_active_versions_with_one_measure_name_exclude_both(env: _Env) -> None:
    _confirm_named(env, "schools_count")
    v1 = env.version()
    other = env.store.reconnect(
        space_id=env.sid,
        identity=SourceIdentity("postgresql", "db2.example.net", "other", "public"),
        fingerprint="fp-other",
    )
    assert other.status == "active"
    env.store.record_snapshot(
        other.id, body=dict(env.snapshot(v1).body), violations=[], verified=True
    )
    twin = env.raw("schools_count", version=other)
    env.store.decide_measure(
        twin.id,
        to_state="confirmed",
        expected_hash=twin.definition_hash,
        evidence={"column_type": ""},
        actor=ACTOR,
        ledger=lambda _payload: "led_twin",
    )
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    assert "schools_count" not in onto.measures  # neither is "the" definition
    assert measure_exclusions(onto) == {"schools_count": "name_collision"}


def test_a_name_that_breaks_the_wire_rule_or_a_sql_looking_description_is_not_sent(
    env: _Env,
) -> None:
    """Cortex 4xxs the WHOLE ask on one bad name or description; DMS never sends one."""
    confirmed = _confirm_named(env, "schools_count")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    row = env.store._measures[idx]
    for field, value, reason in (
        ("description", "select name from schools", "description_invalid"),
        ("description", "Line one\nline two", "description_invalid"),
        ("name", "Bad-Name", "name_invalid"),
    ):
        edited = replace(row, **{field: value})
        env.store._measures[idx] = replace(
            edited,
            definition_hash=measure_definition_hash(
                edited.name, edited.grain, edited.aggregate, edited.column_name, edited.description
            ),
        )
        onto = load_space_ontology(env.space, env.store)
        assert onto is not None and onto.measures == {}, (field, value)
        assert measure_exclusions(onto) == {edited.name: reason}, (field, value)


def test_an_unknown_aggregate_is_skipped_not_raised(env: _Env) -> None:
    confirmed = _confirm_named(env, "schools_count")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    row = env.store._measures[idx]
    env.store._measures[idx] = replace(
        row,
        aggregate="median",
        definition_hash=measure_definition_hash(
            row.name, row.grain, "median", row.column_name, row.description
        ),
    )
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None and onto.measures == {}
    assert measure_exclusions(onto) == {"schools_count": "aggregate_invalid"}


def test_a_failing_measures_read_raises_out_of_load_and_a_bad_row_does_not(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirm_named(env, "schools_count")

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("measures table unreachable")

    monkeypatch.setattr(env.store, "measures_for_space", boom)
    with pytest.raises(RuntimeError, match="unreachable"):
        load_space_ontology(env.space, env.store)  # the caller names ontology_store_unavailable


def test_attach_reads_the_store_once_and_only_confirmed_active_rows(env: _Env) -> None:
    _confirm_named(env, "schools_count")
    calls: list[dict[str, Any]] = []
    real = env.store.measures_for_space

    def spy(space_id: UUID, **kw: Any) -> Any:
        calls.append({"space_id": space_id, **kw})
        return real(space_id, **kw)

    env.store.measures_for_space = spy  # type: ignore[method-assign]
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None and "schools_count" in onto.measures
    assert calls == [{"space_id": env.sid, "states": ("confirmed",), "active_only": True}]


def test_attach_returns_what_it_skipped(env: _Env) -> None:
    confirmed = _confirm_named(env, "schools_count")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    env.store._measures[idx] = replace(env.store._measures[idx], description="Edited.")
    onto = Ontology()
    onto.add_object(SCHOOLS, SCHOOLS, ["school_id"])
    assert attach_confirmed_measures(onto, env.sid, env.store) == {"schools_count": "integrity"}
    assert measure_exclusions(onto) == {"schools_count": "integrity"}
    assert measure_exclusions(None) == {}


# ------------------------------------------------------------------ prune against the lake


def _verified(env: _Env) -> tuple[Ontology, list[Any]]:
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    loaded = ga.load_verified_ontology(env.lake, onto)
    assert loaded is not None
    return loaded, list(ga.cached_verify_violations(loaded) or [])


def _confirm_three(env: _Env) -> None:
    for view in env.proposed().values():
        env.confirm(view)


def test_prune_keeps_a_healthy_measure_and_marks_it_checked(env: _Env) -> None:
    _confirm_three(env)
    onto, violations = _verified(env)
    before = {n: m for n, m in onto.measures.items()}
    assert all(m.provenance and not m.provenance.columns_checked for m in before.values())
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {} and measure_exclusions(onto) == {}
    assert set(onto.measures) == {"schools_budget_sum", "schools_count", "districts_count"}
    for name, m in onto.measures.items():
        assert m.provenance is not None and m.provenance.columns_checked is True
        assert replace(m.provenance, columns_checked=False) == before[name].provenance
        assert m.expression == before[name].expression  # nothing else changed
    assert confirmed_measure_suggestions(onto) == [
        f"districts_count: {onto.measures['districts_count'].description}",
        f"schools_budget_sum: {onto.measures['schools_budget_sum'].description}",
        f"schools_count: {onto.measures['schools_count'].description}",
    ]


def test_suggestions_offer_only_checked_confirmed_measures(env: _Env) -> None:
    _confirm_named(env, "schools_count")
    onto, _v = _verified(env)
    assert confirmed_measure_suggestions(onto) == []  # loaded but not yet checked against the lake
    prune_unusable_measures([onto], [], env.lake, ALLOWED)
    assert len(confirmed_measure_suggestions(onto)) == 1
    assert confirmed_measure_suggestions(None) == []


@pytest.mark.parametrize(
    ("ddl", "reason"),
    [
        ("ALTER TABLE bronze.public_schools DROP COLUMN budget", "column_missing"),
        (
            "ALTER TABLE bronze.public_schools RENAME COLUMN budget TO budget_myr",
            "column_missing",
        ),
        ("ALTER TABLE bronze.public_schools ALTER budget TYPE VARCHAR", "column_type_changed"),
        ("ALTER TABLE bronze.public_schools ALTER budget TYPE DOUBLE", "column_type_changed"),
        (
            "ALTER TABLE bronze.public_schools ALTER budget TYPE DECIMAL(12,2)",
            "column_type_changed",
        ),
    ],
)
def test_prune_removes_a_measure_whose_column_moved(env: _Env, ddl: str, reason: str) -> None:
    _confirm_three(env)
    _lake_exec(env.lake, ddl)
    onto, violations = _verified(env)
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {"schools_budget_sum": reason}
    assert measure_exclusions(onto) == {"schools_budget_sum": reason}
    # the measures that do not depend on that column are untouched
    assert set(onto.measures) == {"schools_count", "districts_count"}
    assert all(m.provenance and m.provenance.columns_checked for m in onto.measures.values())


def test_prune_removes_a_measure_with_no_recorded_type(env: _Env) -> None:
    confirmed = _confirm_named(env, "schools_budget_sum")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    env.store._measures[idx] = replace(env.store._measures[idx], evidence={})
    onto, violations = _verified(env)
    prov = onto.measures["schools_budget_sum"].provenance
    assert prov is not None and prov.column_type == ""
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {"schools_budget_sum": "column_type_changed"}  # "unchanged" cannot be shown


def test_prune_removes_every_measure_on_a_grain_that_fails_verify(env: _Env) -> None:
    _confirm_three(env)
    _lake_exec(
        env.lake,
        "INSERT INTO bronze.public_schools SELECT * FROM bronze.public_schools WHERE school_id = 1",
    )
    onto, violations = _verified(env)
    assert {v.subject for v in violations} == {SCHOOLS}  # the key is no longer unique
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {
        "schools_budget_sum": "grain_unverified",
        "schools_count": "grain_unverified",
    }
    assert set(onto.measures) == {"districts_count"}


def test_prune_keeps_a_measure_when_only_a_link_failed(env: _Env) -> None:
    """An orphan FK breaks grouping through the link, not the ungrouped total at the grain."""
    _confirm_three(env)
    _lake_exec(
        env.lake,
        "INSERT INTO bronze.public_schools (school_id, district_id, name, budget) "
        "VALUES (5, 99, 'Elm', 9.25)",
    )
    onto, violations = _verified(env)
    assert {v.check for v in violations} == {"fk_intact"}
    assert prune_unusable_measures([onto], violations, env.lake, ALLOWED) == {}
    assert set(onto.measures) == {"schools_budget_sum", "schools_count", "districts_count"}


def test_prune_removes_a_measure_on_a_table_this_turn_is_not_granted(env: _Env) -> None:
    _confirm_three(env)
    onto, violations = _verified(env)
    removed = prune_unusable_measures([onto], violations, env.lake, {DISTRICTS})
    assert removed == {
        "schools_budget_sum": "grain_not_granted",
        "schools_count": "grain_not_granted",
    }
    assert set(onto.measures) == {"districts_count"}
    # the grant compare is the table the compile reads, case-blind
    onto2, v2 = _verified(env)
    assert (
        prune_unusable_measures([onto2], v2, env.lake, {SCHOOLS.upper(), DISTRICTS.upper()}) == {}
    )


@pytest.mark.parametrize("lake_kind", ["none", "missing"])
def test_prune_removes_everything_when_the_lake_cannot_be_read(
    env: _Env, tmp_path: Path, lake_kind: str
) -> None:
    _confirm_three(env)
    onto, violations = _verified(env)
    lake = None if lake_kind == "none" else tmp_path / "nope.duckdb"
    removed = prune_unusable_measures([onto], violations, lake, ALLOWED)
    # even COUNT(*): "no lake" is not "the measure still holds"
    assert set(removed.values()) == {"lake_unreadable"} and len(removed) == 3
    assert onto.measures == {}


def test_prune_a_relation_that_no_longer_describes_is_lake_unreadable(env: _Env) -> None:
    _confirm_three(env)
    onto, violations = _verified(env)
    _lake_exec(env.lake, "DROP TABLE bronze.public_districts")
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {"districts_count": "lake_unreadable"}
    assert set(onto.measures) == {"schools_budget_sum", "schools_count"}


def test_prune_rejects_a_hostile_column_with_a_recomputed_hash_and_leaves_the_lake_alone(
    env: _Env,
) -> None:
    confirmed = _confirm_named(env, "schools_budget_sum")
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    hostile = 'x"); DROP TABLE bronze.public_schools; --'
    row = env.store._measures[idx]
    env.store._measures[idx] = replace(
        row,
        column_name=hostile,
        definition_hash=measure_definition_hash(
            row.name, row.grain, row.aggregate, hostile, row.description
        ),
    )
    before = _tables_and_types(env.lake)
    onto, violations = _verified(env)
    assert "schools_budget_sum" in onto.measures  # the hash alone cannot catch this writer
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {"schools_budget_sum": "column_missing"}
    assert _tables_and_types(env.lake) == before  # nothing was executed against the lake
    assert _count(env.lake, "bronze.public_schools") == 4


def _tables_and_types(lake: Path) -> dict[str, dict[str, str]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        out: dict[str, dict[str, str]] = {}
        for schema, table in con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = 'bronze' ORDER BY 1, 2"
        ).fetchall():
            cols = con.execute(f'DESCRIBE SELECT * FROM "{schema}"."{table}"').fetchall()
            out[f"{schema}.{table}"] = {str(c[0]): str(c[1]) for c in cols}
        return out
    finally:
        con.close()


def _count(lake: Path, relation: str) -> int:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


def test_prune_without_measures_does_no_work_and_never_opens_the_lake(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inert until a steward confirms something: no measures, no connection, no query."""
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None and onto.measures == {}

    opened: list[Path] = []

    def spy(path: Path) -> Any:  # recorded, not raised: the loader swallows a connect error
        opened.append(path)
        raise OSError("prune opened the lake with nothing to check")

    monkeypatch.setattr(mu, "connect_file", spy)
    assert prune_unusable_measures([onto, None, onto], [], env.lake, ALLOWED) == {}
    assert prune_unusable_measures([], [], None, set()) == {}
    assert prune_unusable_measures([None], [], None, set()) == {}
    assert opened == []


def test_prune_handles_one_ontology_given_twice_and_opens_the_lake_once(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirm_three(env)
    onto, violations = _verified(env)
    opened: list[Path] = []
    real = mu.connect_file

    def counting(path: Path) -> Any:
        opened.append(path)
        return real(path)

    monkeypatch.setattr(mu, "connect_file", counting)
    # `onto` and `declared` are the same object in the product; the list says so twice
    assert prune_unusable_measures([onto, onto], violations, env.lake, ALLOWED) == {}
    assert len(opened) == 1


def test_prune_removes_a_measure_nothing_vouches_for(env: _Env) -> None:
    onto, violations = _verified(env)
    onto.add_measure("loose_count", SCHOOLS, "COUNT(*)")  # no provenance: never confirmed
    removed = prune_unusable_measures([onto], violations, env.lake, ALLOWED)
    assert removed == {"loose_count": "integrity"} and onto.measures == {}


def test_prune_reads_the_cached_verify_violations_as_well_as_the_ones_it_is_given(
    env: _Env,
) -> None:
    _confirm_three(env)
    _lake_exec(
        env.lake,
        "INSERT INTO bronze.public_schools SELECT * FROM bronze.public_schools WHERE school_id = 1",
    )
    onto, _violations = _verified(env)
    removed = prune_unusable_measures([onto], [], env.lake, ALLOWED)  # caller passed none
    assert set(removed) == {"schools_budget_sum", "schools_count"}


# ------------------------------------------------------------------ rule 12 at ask time


def _plan(*filters: tuple[str, str, str, Any]) -> ga.QueryPlan:
    return ga.QueryPlan(measure="schools_count", filters=filters)


def _lake_onto(env: _Env) -> Ontology:
    onto, _violations = _verified(env)
    return onto


def test_filter_values_that_exist_exactly_are_present(env: _Env) -> None:
    onto = _lake_onto(env)
    assert filter_values_present(_plan((DISTRICTS, "name", "=", "North")), onto, env.lake) is None
    assert (
        filter_values_present(_plan((DISTRICTS, "name", "IN", ["North", "South"])), onto, env.lake)
        is None
    )
    assert filter_values_present(_plan((SCHOOLS, "school_id", "=", 4)), onto, env.lake) is None
    assert filter_values_present(_plan(), onto, env.lake) is None


@pytest.mark.parametrize("literal", ["north", "NORTH", "North ", " North", "Nor", "Nth", ""])
def test_a_filter_literal_that_differs_in_case_or_whitespace_is_not_a_match(
    env: _Env, literal: str
) -> None:
    onto = _lake_onto(env)
    got = filter_values_present(_plan((DISTRICTS, "name", "=", literal)), onto, env.lake)
    assert got is not None and got.startswith("unmatched_filter_value: "), got
    assert f"{DISTRICTS}.name" in got and "exactly as written" in got


def test_one_missing_value_in_an_in_list_is_enough(env: _Env) -> None:
    onto = _lake_onto(env)
    got = filter_values_present(_plan((DISTRICTS, "name", "IN", ["North", "east"])), onto, env.lake)
    assert got is not None and got.startswith("unmatched_filter_value: ") and "'east'" in got
    assert "'North'" not in got  # it names the value that is missing, not the ones that are there
    lowered = filter_values_present(
        _plan((DISTRICTS, "name", "in", ["North", "south"])), onto, env.lake
    )
    assert (
        lowered is not None and "'south'" in lowered
    )  # the operator is case-blind, as compile's is
    assert filter_values_present(_plan((SCHOOLS, "school_id", "=", 99)), onto, env.lake) is not None


def test_a_null_literal_matches_nothing(env: _Env) -> None:
    onto = _lake_onto(env)
    assert filter_values_present(_plan((SCHOOLS, "name", "=", None)), onto, env.lake) is not None


def test_only_equality_and_in_are_probed(env: _Env) -> None:
    onto = _lake_onto(env)
    plan = _plan(
        (SCHOOLS, "school_id", ">", 10_000),
        (SCHOOLS, "name", "LIKE", "zzz%"),
        (SCHOOLS, "school_id", "<>", -1),
    )
    assert filter_values_present(plan, onto, env.lake) is None  # an empty range is honest


def test_an_unknown_object_or_column_is_left_to_the_compiler(env: _Env) -> None:
    onto = _lake_onto(env)
    assert filter_values_present(_plan(("bronze.nope", "x", "=", 1)), onto, env.lake) is None
    assert filter_values_present(_plan((SCHOOLS, "no_such_column", "=", 1)), onto, env.lake) is None


def test_too_many_in_literals_fail_closed(env: _Env) -> None:
    onto = _lake_onto(env)
    fifty = filter_values_present(
        _plan((SCHOOLS, "school_id", "IN", list(range(1, 51)))), onto, env.lake
    )
    assert fifty is not None and "at most 50" not in fifty  # probed: ids 5.. are not there
    many = filter_values_present(_plan((SCHOOLS, "school_id", "IN", [1] * 51)), onto, env.lake)
    assert many is not None and "at most 50" in many  # unchecked is not "present"


def test_an_unreadable_lake_fails_closed(env: _Env, tmp_path: Path) -> None:
    onto = _lake_onto(env)
    plan = _plan((DISTRICTS, "name", "=", "North"))
    for lake in (None, tmp_path / "nope.duckdb"):
        got = filter_values_present(plan, onto, lake)
        assert got is not None and got.startswith("unmatched_filter_value: ")
        assert "not checked" in got


def test_a_type_that_cannot_be_compared_fails_closed(env: _Env) -> None:
    onto = _lake_onto(env)
    got = filter_values_present(_plan((SCHOOLS, "school_id", "=", "abc")), onto, env.lake)
    assert got is not None and got.startswith("unmatched_filter_value: ")


def test_a_literal_with_a_quote_is_a_literal_not_sql(env: _Env) -> None:
    onto = _lake_onto(env)
    evil = "x'; DROP TABLE bronze.public_districts; --"
    got = filter_values_present(_plan((DISTRICTS, "name", "=", evil)), onto, env.lake)
    assert got is not None and got.startswith("unmatched_filter_value: ")
    assert _count(env.lake, DISTRICTS) == 2  # still there


def test_the_probe_is_one_query_per_distinct_literal(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    onto = _lake_onto(env)
    seen: list[str] = []
    real = mu.connect_file

    class _Spy:
        def __init__(self, con: Any) -> None:
            self.con = con

        def execute(self, sql: str, *a: Any) -> Any:
            seen.append(sql)
            return self.con.execute(sql, *a)

        def close(self) -> None:
            self.con.close()

    monkeypatch.setattr(mu, "connect_file", lambda p: _Spy(real(p)))
    plan = _plan(
        (DISTRICTS, "name", "IN", ["North", "South", "North"]),
        (DISTRICTS, "name", "=", "North"),
    )
    assert filter_values_present(plan, onto, env.lake) is None
    probes = [s for s in seen if "LIMIT 1" in s and "LIMIT 0" not in s]
    scans = [s for s in seen if "bool_or" in s]
    assert len(probes) == 2  # North once, South once
    assert len(scans) == 2  # and one scan of the other columns for each distinct literal


# ------------------------------------------------------------------ the wire (E18)


def _ontology_with_measures(n: int, *, desc: str, grain: str) -> Ontology:
    onto = Ontology()
    onto.add_object(grain, grain, ["id"])
    for i in range(n):
        name = f"m{i:02d}_".ljust(48, "x")
        assert len(name) == 48
        onto.add_measure(name, grain, "COUNT(*)", description=desc)
    return onto


def _worst_grain() -> str:
    return "bronze." + "g" * 57  # 64 characters


def test_worst_case_eighty_measures_fit_the_measure_budget_and_the_cortex_limit() -> None:
    grain = _worst_grain()
    onto = _ontology_with_measures(80, desc="d" * 200, grain=grain)
    space = space_catalog(onto, [], {grain})
    assert len(space["measures"]) == 80
    used = 12 * 1024  # a typical tables/schema/schema_context share of the body
    block = budget_space_block(space, used=used)
    assert len(block["measures"]) == 80 and "measures_truncated" not in block
    assert len(json.dumps(block["measures"])) <= WIRE_MAX_MEASURE_BYTES
    assert used + len(json.dumps(block)) <= 64 * 1024  # Cortex MAX_ONTOLOGY_BYTES
    assert used + len(json.dumps(block)) <= WIRE_MAX_BYTES
    assert set(next(iter(block["measures"].values()))) == {"grain", "description"}


def test_wide_character_descriptions_are_truncated_by_name_order_and_flagged() -> None:
    """json.dumps escapes each non-ASCII character to six bytes, so 80 descriptions of 200
    can exceed 32 KiB: measures are cut (name order), flagged, and the rest still fits."""
    grain = _worst_grain()
    onto = _ontology_with_measures(80, desc="é" * 200, grain=grain)
    space = space_catalog(onto, [], {grain})
    block = budget_space_block(space, used=12 * 1024)
    assert block["measures_truncated"] is True
    kept = list(block["measures"])
    assert 0 < len(kept) < 80
    assert kept == sorted(space["measures"])[: len(kept)]  # the first names in order survive
    assert len(json.dumps(block["measures"])) <= WIRE_MAX_MEASURE_BYTES
    assert 12 * 1024 + len(json.dumps(block)) <= 64 * 1024


def test_measures_are_budgeted_before_objects_and_links() -> None:
    measures = {f"m{i:02d}": {"grain": "bronze.t0", "description": "x" * 200} for i in range(80)}
    space = {
        "objects": {f"bronze.t{i}": {"key": [f"id_{i}"]} for i in range(400)},
        "links": {},
        "measures": measures,
        "verified": True,
    }
    block = budget_space_block(space, used=30 * 1024)
    assert len(block["measures"]) == 80 and "measures_truncated" not in block  # first in line
    assert block["space_truncated"] is True and 0 < len(block["objects"]) < 400
    assert 30 * 1024 + len(json.dumps(block)) <= 64 * 1024


def test_the_measure_budget_never_exceeds_what_is_left_of_the_body() -> None:
    measures = {f"m{i:02d}": {"grain": "bronze.t0", "description": "x" * 200} for i in range(80)}
    space = {"objects": {}, "links": {}, "measures": measures, "verified": True}
    block = budget_space_block(space, used=WIRE_MAX_BYTES - 1000)
    assert block["measures_truncated"] is True and len(block["measures"]) < 80
    assert WIRE_MAX_BYTES - 1000 + len(json.dumps(block)) <= 64 * 1024


def test_no_measures_gives_the_block_it_always_gave() -> None:
    space = {
        "objects": {"bronze.a": {"key": ["id"]}},
        "links": {},
        "measures": {},
        "verified": True,
    }
    assert budget_space_block(space, used=100) == {
        "objects": {"bronze.a": {"key": ["id"]}},
        "links": {},
        "measures": {},
        "verified": True,
    }


def test_a_measure_name_off_the_wire_identifier_rule_never_reaches_the_wire() -> None:
    grain = "bronze.t"
    onto = Ontology()
    onto.add_object(grain, grain, ["id"])
    for name in ("good_name", "bad name", "bad-name", "9starts_with_digit", "x\nnewline", ""):
        onto.add_measure(name, grain, "COUNT(*)", description="ok")
    space = space_catalog(onto, [], {grain})
    assert set(space["measures"]) == {"good_name"}


def test_the_wire_carries_a_measures_grain_and_description_and_nothing_else() -> None:
    onto = _ontology_with_measures(1, desc="One row of the grain.", grain="bronze.t")
    (spec,) = space_catalog(onto, [], {"bronze.t"})["measures"].values()
    assert spec == {"grain": "bronze.t", "description": "One row of the grain."}  # no expression


# ---------------------------------------------------------- demo aliases never reach a Space


def test_a_space_ranking_reads_no_demo_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str] | None] = []

    def fake(payload: Any, allowed: Any = None, *, aliases: Any = None, **_kw: Any) -> None:
        seen.append(aliases)
        return None

    monkeypatch.setattr(ga, "query_plan_from_insights_ranking", fake)
    onto = Ontology()
    onto.add_object(SCHOOLS, SCHOOLS, ["school_id"])
    onto.add_measure("stock_value_myr", SCHOOLS, "COUNT(*)")  # a name the demo pack aliases onto
    payload = {"ontology": {"metrics": [{"id": "total_spend"}]}}
    ga.ontology_plan_from_ranking("total spend", payload, onto=onto, ctx={})
    ga.ontology_plan_from_ranking("total spend", payload, onto=onto, ctx={}, demo=False)
    assert seen[0] and seen[0].get("total_spend") == "stock_value_myr"  # the demo default
    assert seen[1] == {}  # a Space: no demo-pack alias can resolve onto its measures


def test_the_missing_metric_gap_reads_no_demo_aliases_on_a_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_executor import gen_path_refuse as gpr

    seen: list[dict[str, str] | None] = []

    def fake(payload: Any, allowed: Any = None, *, aliases: Any = None, **_kw: Any) -> None:
        seen.append(aliases)
        return None

    monkeypatch.setattr(gpr, "query_plan_from_insights_ranking", fake)
    onto = Ontology()
    onto.add_object(SCHOOLS, SCHOOLS, ["school_id"])
    onto.add_measure("stock_value_myr", SCHOOLS, "COUNT(*)")
    payload = {"ontology": {"metrics": [{"id": "total_spend"}]}}
    gpr.ranking_missing_metric_gap("total spend", payload, onto=onto)
    gpr.ranking_missing_metric_gap("total spend", payload, onto=onto, demo=False)
    assert seen[0] and seen[0].get("total_spend") == "stock_value_myr"  # the demo default
    assert seen[1] == {}


# ------------------------------------------------------------------ structure


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_measure_use_imports_neither_the_ask_nor_the_steward_service() -> None:
    """``generative_ask`` and ``space_ontology`` import ``measure_use``; ``measure_confirm``
    imports ``generative_ask``. ``measure_use`` closing the loop would be an import cycle."""
    path = EXECUTOR_DIR / "measure_use.py"
    assert not {m for m in _imports(path) if "generative_ask" in m or "measure_confirm" in m}
    # space_ontology is named for typing only, inside ``if TYPE_CHECKING``: never at import time
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert "space_ontology" not in ast.dump(node)


def test_the_ask_and_the_ontology_loader_import_measure_use() -> None:
    assert "dms_executor.measure_use" in _imports(EXECUTOR_DIR / "generative_ask.py")
    assert "dms_executor.measure_use" in _imports(EXECUTOR_DIR / "space_ontology.py")


def test_measure_use_has_no_sql_parser_and_does_not_reimplement_canonicalisation() -> None:
    text = (EXECUTOR_DIR / "measure_use.py").read_text()
    assert "sqlglot" not in text and "canonical_manifest_bytes" not in text
    assert "to_excel" not in text


def test_a_fresh_uuid_space_has_no_ontology_so_nothing_loads() -> None:
    assert load_space_ontology(str(uuid4()), OntologyStore()) is None
    assert load_space_ontology(None) is None
    assert load_space_ontology("not-a-uuid") is None


@pytest.mark.parametrize(
    ("question", "measure"),
    [
        ("What is the total budget per school?", BUDGET),
        ("What is the total budget of each school?", BUDGET),
        ("What is the total budget of schools excluding the north?", BUDGET),
        ("What is the total budget of schools except the north?", BUDGET),
        ("What is the total budget of schools without the north?", BUDGET),
        ("What is the total budget of schools only in the north?", BUDGET),
        ("What is the total budget of schools not in the north?", BUDGET),
        ("What is the total budget of schools other than the north?", BUDGET),
        ("What is the total budget of schools by district?", BUDGET),
        ("How many schools per district?", SCOUNT),
        ("How many schools by", SCOUNT),  # a dangling "by" names nothing, so it is not covered
    ],
)
def test_fit_a_qualifier_the_measure_does_not_name_is_not_ok(
    question: str, measure: Measure
) -> None:
    got = measure_fit(question, _pool(measure))
    assert got == FitVerdict(False, measure.name, "qualifier_cue"), question


def test_fit_a_by_phrase_is_covered_when_the_measure_names_its_word() -> None:
    assert measure_fit("What is the total budget by schools?", _pool(BUDGET)).ok is True


def test_fit_a_qualifier_word_in_the_measure_name_is_covered() -> None:
    per = _m("budget_per_student_avg", "avg", "budget_per_student")
    assert measure_fit("average budget per student", _pool(per)) == FitVerdict(
        True, "budget_per_student_avg", "ok"
    )
    # but not another qualifier next to it
    got = measure_fit("average budget per student excluding the north", _pool(per))
    assert got == FitVerdict(False, "budget_per_student_avg", "qualifier_cue")
    by = _m("students_by_district_count", "count", "*")
    assert measure_fit("How many students by district?", _pool(by)).ok is True
    assert measure_fit("How many students by grade by district?", _pool(by)).reason == (
        "qualifier_cue"
    )


def test_fit_the_verifier_cases() -> None:
    assert measure_fit("budget per student", _pool(_m("student_budget_sum", "sum"))).reason == (
        "qualifier_cue"
    )
    assert measure_fit("budget excluding the north", _pool(BUDGET)).reason == "no_match"
    assert measure_fit("schools budget excluding the north", _pool(BUDGET)).reason == (
        "qualifier_cue"
    )
    assert measure_fit("average total budget of schools", _pool(BUDGET)).reason == "aggregate_cue"
    students_count = _m("students_count", "count", "*")
    assert measure_fit("total number of students", _pool(students_count)).ok is True


def test_fit_an_ordinary_question_is_still_ok() -> None:
    for q in (
        "What is the total budget of all schools?",
        "total budget of schools",
        "What is the budget of schools in the north?",
    ):
        assert measure_fit(q, _pool(BUDGET)) == FitVerdict(True, "schools_budget_sum", "ok"), q


# ------------------------------------------------------------------ a grouping the plan computes


@pytest.mark.parametrize(
    ("question", "measure", "grouped"),
    [
        ("What is the total budget of schools by district?", BUDGET, ((DISTRICTS, "name"),)),
        ("What is the total budget of schools by district?", BUDGET, ((SCHOOLS, "district_id"),)),
        ("What is the total budget of schools per district?", BUDGET, ((DISTRICTS, "name"),)),
        ("What is the total budget of schools for each district?", BUDGET, ((DISTRICTS, "name"),)),
        ("How many schools per district?", SCOUNT, ((DISTRICTS, "name"),)),
    ],
)
def test_fit_a_by_per_or_each_word_is_covered_by_a_plan_that_groups_by_it(
    question: str, measure: Measure, grouped: tuple[tuple[str, str], ...]
) -> None:
    got = measure_fit(question, _pool(measure), grouped_by=grouped)
    assert got == FitVerdict(True, measure.name, "ok"), question


@pytest.mark.parametrize(
    ("question", "grouped"),
    [
        ("What is the total budget of schools by district?", ()),  # the plan does not group
        ("What is the total budget of schools by region?", ((DISTRICTS, "name"),)),  # not that
        ("What is the total budget of schools per student?", ((DISTRICTS, "name"),)),
        ("What is the total budget of schools by", ((DISTRICTS, "name"),)),  # dangling by
    ],
)
def test_fit_a_grouping_that_is_not_the_word_asked_covers_nothing(
    question: str, grouped: tuple[tuple[str, str], ...]
) -> None:
    got = measure_fit(question, _pool(BUDGET), grouped_by=grouped)
    assert got == FitVerdict(False, BUDGET.name, "qualifier_cue"), question


@pytest.mark.parametrize(
    "question",
    [
        "What is the total budget of schools excluding the district?",
        "What is the total budget of schools except the district?",
        "What is the total budget of schools without the district?",
        "What is the total budget of schools only in the district?",
        "What is the total budget of schools not in the district?",
        "What is the total budget of schools other than the district?",
    ],
)
def test_fit_a_grouping_never_covers_a_restriction(question: str) -> None:
    """Only ``by`` / ``per`` / ``each`` are breakdown words. 'excluding the district' narrows the
    number; a plan grouped by district does not do that, so the veto stands."""
    got = measure_fit(question, _pool(BUDGET), grouped_by=((DISTRICTS, "name"),))
    assert got == FitVerdict(False, BUDGET.name, "qualifier_cue"), question


# ------------------------------------- exact accounting: the question and the plan agree (fit)

NORTH_FILTER = ("bronze.public_districts", "name", "=", "North")


def _acct(question: str, **kw: Any) -> mu.Accounting:
    kw.setdefault("tables", SOURCE_TABLES)
    return mu.account_for_question(question, measure="schools_budget_sum", grain=SCHOOLS, **kw)


@pytest.mark.parametrize(
    "question",
    [
        "What is the total budget of all schools?",
        "total budget of schools",
        "What is the overall budget for the schools?",
        "Show me the total budget of schools",
        "show me the total budget of schools",
        "Tell me the total budget of schools, please.",
        "Can you give me the sum of the budgets of all schools?",
        "Could you please show me the total budget of schools?!",
        "How much is the budget of the schools",
        "Total budget of schools",
        "List all the schools budget",
    ],
)
def test_fit_a_plain_question_with_no_plan_condition_is_ok(question: str) -> None:
    assert _acct(question).ok, _acct(question)
    assert measure_fit(question, _pool(BUDGET), filters=()).ok is True


@pytest.mark.parametrize(
    ("question", "words"),
    [
        ("What is the total budget of schools in the north district?", ("'north'", "'district'")),
        ("What is the total budget of schools with a budget over 100?", ("'over'", "'100'")),
        ("What is the total budget of schools in district 2?", ("'district'", "'2'")),
        ("What is the total budget of schools last year?", ("'last'", "'year'")),
        ("What is the total budget of schools at most 100?", ("'most'", "'100'")),
        ("What is the total budget of schools between 5 and 9?", ("'between'", "'5'", "'9'")),
        ("What is the total budget of schools in the database?", ("'database'",)),
        ("What is the total budget of schools in the US?", ("'US'",)),
        ("What is the total budget of schools in the us?", ("'us'",)),
        ("What is the total budget of schools in OR?", ("'OR'",)),
        ("What is the total budget of schools in IT?", ("'IT'",)),
        ("What is the total budget of schools in IN?", ("'IN'",)),
        ("What is the total budget of schools in A?", ("'A'",)),
        ("What is the total budget of Wills schools?", ("'Wills'",)),
        ("What is the total budget of Doe schools?", ("'Doe'",)),
        ("What is the total budget of Cans schools?", ("'Cans'",)),
        (
            "What is the total budget of schools where region is north?",
            ("'where'", "'region'", "'north'"),
        ),
        ("Show us the total budget of schools", ("'us'",)),
    ],
)
def test_fit_a_condition_the_plan_does_not_carry_is_unbound(
    question: str, words: tuple[str, ...]
) -> None:
    got = measure_fit(question, _pool(BUDGET), filters=())
    assert (got.ok, got.pick, got.reason) == (False, "schools_budget_sum", "unbound_constraint")
    assert got.detail == ", ".join(words), got.detail


def test_fit_a_plan_that_carries_the_condition_covers_it() -> None:
    q = "What is the total budget of schools in the North district?"
    assert measure_fit(q, _pool(BUDGET), filters=[NORTH_FILTER]).ok is True
    # a JSON plan carries its filters as lists, and IN carries a list of literals
    assert measure_fit(q, _pool(BUDGET), filters=[list(NORTH_FILTER)]).ok is True
    both = ("bronze.public_districts", "name", "IN", ["North", "South"])
    q2 = "What is the total budget of schools in the North and South districts?"
    assert measure_fit(q2, _pool(BUDGET), filters=[both]).ok is True
    by_id = ("bronze.public_schools", "district_id", "=", 2)
    q3 = "What is the total budget of schools in district 2?"
    assert measure_fit(q3, _pool(BUDGET), filters=[by_id]).ok is True


def test_fit_a_plan_that_carries_only_part_of_the_condition_is_unbound() -> None:
    q = "What is the total budget of schools in the North and South districts?"
    got = measure_fit(q, _pool(BUDGET), filters=[NORTH_FILTER])
    assert (got.ok, got.reason, got.detail) == (False, "unbound_constraint", "'South'")


def test_fit_a_filter_word_is_not_a_license_for_another_word() -> None:
    got = measure_fit(
        "What is the total budget of schools in the North region?",
        _pool(BUDGET),
        filters=[NORTH_FILTER],
    )
    assert (got.ok, got.detail) == (False, "'region'")


def test_fit_a_group_by_covers_the_breakdown_word_and_only_that() -> None:
    by_district = [("bronze.public_districts", "name")]
    ok = "What is the total budget of all schools for every district?"
    assert measure_fit(ok, _pool(BUDGET), grouped_by=by_district, filters=()).ok is True
    assert measure_fit(
        "What is the total budget of schools by district?",
        _pool(BUDGET),
        grouped_by=by_district,
        filters=(),
    ).ok is True
    # a plan that does not group by it covers nothing; "by schools" is not a grouping either
    for q in (
        "What is the total budget of schools by schools?",
        "What is the total budget of schools by region?",
    ):
        got = measure_fit(q, _pool(BUDGET), grouped_by=by_district, filters=())
        assert got.ok is False, q


def test_fit_without_a_plan_the_accounting_is_not_made() -> None:
    """``filters=None`` is a caller with no plan (a listing, a ranking probe): unchanged."""
    q = "What is the total budget of schools in the north district?"
    assert measure_fit(q, _pool(BUDGET)).ok is True


def test_fit_ranking_and_range_phrasing_is_never_accounted_for() -> None:
    by_district = [(DISTRICTS, "name")]
    q = "What are the top 3 districts for the total budget of schools?"
    got = measure_fit(q, _pool(BUDGET), grouped_by=by_district, filters=())
    assert (got.ok, got.reason) == (False, "unbound_constraint")
    assert got.detail == "'top', '3'", got.detail


# ------------------------------- a condition in ANY script is a token, never invisible

NON_ASCII_CONDITIONS = [
    pytest.param("\u5317\u533a", id="cjk"),
    pytest.param("\u0b95\u0bc7\u0bb2\u0bbe", id="tamil"),
    pytest.param("\U0001f334", id="emoji"),
    pytest.param("\u00f1orth", id="latin-diacritic"),
    pytest.param("\u5317 district", id="cjk-then-ascii"),
    pytest.param("district \uff12", id="fullwidth-digit"),
    pytest.param("\uff35\uff33", id="fullwidth-capitals"),
]


@pytest.mark.parametrize("condition", NON_ASCII_CONDITIONS)
def test_fit_a_condition_in_any_script_is_unbound(condition: str) -> None:
    q = f"What is the total budget of schools in {condition}?"
    got = measure_fit(q, _pool(BUDGET), filters=())
    assert (got.ok, got.pick, got.reason) == (False, "schools_budget_sum", "unbound_constraint")
    assert got.detail, "the refusal names what nothing accounts for"


@pytest.mark.parametrize(
    "mark",
    ["/", "&", "+", "$", "<", ">", "~", "*", "@", "#", "|", "\u00f7", "\u00d7", "-", "`", "'", '"',
     "(", ")", ":", ";", "_", "\u2014", "\u200b", "="],
)  # fmt: skip
def test_fit_every_character_outside_the_closed_punctuation_set_is_refused(mark: str) -> None:
    """Dashes, quotes, brackets, backticks, currency, operators and zero-width marks are not
    harmless: only ``? . ,`` and a final ``!`` separate, and the refusal names the class."""
    q = f"What is the total budget {mark} schools?"
    got = measure_fit(q, _pool(BUDGET), filters=())
    assert (got.ok, got.reason) == (False, "unbound_constraint"), mark
    assert "(" in got.detail and got.detail.endswith(")"), got.detail


def test_fit_the_refusal_names_the_character_class() -> None:
    assert _acct("What is the total budget / schools?").unaccounted == ("'/' (punctuation)",)
    assert _acct("What is the total budget $ schools?").unaccounted == ("'$' (symbol)",)
    assert _acct("What is the total budget \U0001f334 schools?").unaccounted == (
        "'\U0001f334' (symbol)",
    )
    assert _acct("What is the total budget \u200b schools?").unaccounted == (
        repr("\u200b") + " (control or format character)",
    )


@pytest.mark.parametrize(
    "question",
    [
        "What is the total budget of schools?",
        "What is the total budget of schools, please?",
        "What is the total budget of schools.",
        "What is the total budget of schools!",
        "What is the total budget of schools ?",
        "What is the total budget of schools,\tall of the schools\n",
        "What is the total budget of schools\uff1f",  # NFKC folds the fullwidth mark to ?
        "What is the total budget of schools\u2026",  # NFKC: three full stops
    ],
)
def test_fit_the_closed_punctuation_set_only_separates(question: str) -> None:
    assert measure_fit(question, _pool(BUDGET), filters=()).ok is True


@pytest.mark.parametrize(
    "question",
    [
        "What is the total budget of schools!!",
        "What is the total budget! of schools",
        "What's the total budget of schools?",
        "what\u2019s the total school budget",
    ],
)
def test_fit_a_bang_that_is_not_final_and_every_contraction_is_refused(question: str) -> None:
    got = measure_fit(question, _pool(BUDGET), filters=())
    assert (got.ok, got.reason) == (False, "unbound_constraint"), question


def test_fit_a_plan_that_carries_the_non_ascii_literal_covers_it() -> None:
    q = "What is the total budget of schools in \u5317\u533a?"
    flt = ("bronze.public_districts", "name", "=", "\u5317\u533a")
    assert measure_fit(q, _pool(BUDGET), filters=[flt]).ok is True
    other = ("bronze.public_districts", "name", "=", "\u5357\u533a")
    got = measure_fit(q, _pool(BUDGET), filters=[other])
    assert (got.ok, got.reason, got.detail) == (False, "unstated_filter", "'\u5357\u533a'")


def test_fit_a_fullwidth_digit_is_the_digit_it_looks_like() -> None:
    """NFKC folds the fullwidth 2 to 2, in the question and in the plan."""
    by_id = ("bronze.public_schools", "district_id", "=", 2)
    q = "What is the total budget of schools in district \uff12?"
    assert measure_fit(q, _pool(BUDGET), filters=[by_id]).ok is True
    assert measure_fit(q, _pool(BUDGET), filters=()).reason == "unbound_constraint"


# ---------------------------------- the mirror: the plan states nothing the question did not


def test_mirror_a_plan_value_the_question_never_states_is_unstated() -> None:
    q = "What is the total budget of schools in the North district?"
    both = ("bronze.public_districts", "name", "IN", ["North", "South"])
    got = measure_fit(q, _pool(BUDGET), filters=[both])
    assert (got.ok, got.pick, got.reason, got.detail) == (
        False, "schools_budget_sum", "unstated_filter", "'South'",
    )  # fmt: skip
    q2 = "What is the total budget of schools in the North and South districts?"
    assert measure_fit(q2, _pool(BUDGET), filters=[both]).ok is True


@pytest.mark.parametrize("op", [">", ">=", "<", "<=", "<>", "!=", "LIKE", "like", "BETWEEN", ""])
def test_mirror_only_equals_and_in_state_a_value(op: str) -> None:
    q = "What is the total budget of schools in district 2?"
    flt = ("bronze.public_schools", "district_id", op, 2)
    got = measure_fit(q, _pool(BUDGET), filters=[flt])
    assert (got.ok, got.reason) == (False, "unstated_filter")
    assert got.detail == repr(f"operator {op.lower() or 'none'}")


@pytest.mark.parametrize("op", ["=", "in", " IN ", "In"])
def test_mirror_the_two_stated_operators_are_case_and_space_blind(op: str) -> None:
    q = "What is the total budget of schools in district 2?"
    flt = ("bronze.public_schools", "district_id", op, [2] if op.strip().upper() == "IN" else 2)
    assert measure_fit(q, _pool(BUDGET), filters=[flt]).ok is True


@pytest.mark.parametrize(
    "value",
    [None, "", "  ", "--", True, False, [["North"]], {"a": 1}, [], float("nan"), float("inf")],
)
def test_mirror_a_literal_no_question_can_state_is_unstated(value: object) -> None:
    q = "What is the total budget of schools in the North district?"
    flt = ("bronze.public_districts", "name", "IN" if isinstance(value, list) else "=", value)
    got = measure_fit(q, _pool(BUDGET), filters=[flt])
    assert (got.ok, got.reason) == (False, "unstated_filter"), value


def test_mirror_a_set_literal_names_what_is_missing_in_a_stable_order() -> None:
    q = "What is the total budget of schools in the North district?"
    flt = ("bronze.public_districts", "name", "IN", {"South", "West", "North"})
    got = measure_fit(q, _pool(BUDGET), filters=[flt])
    assert got.detail == "'South', 'West'"


def test_mirror_a_filter_with_too_few_parts_fails_closed() -> None:
    q = "What is the total budget of schools in the North district?"
    got = measure_fit(q, _pool(BUDGET), filters=[("bronze.public_districts", "name", "=")])
    assert (got.reason, got.detail) == ("unstated_filter", "'an unreadable filter'")


def test_mirror_a_scalar_with_a_list_value_is_not_a_stated_equality() -> None:
    q = "What is the total budget of schools in the North district?"
    flt = ("bronze.public_districts", "name", "=", ["North"])
    got = measure_fit(q, _pool(BUDGET), filters=[flt])
    assert got.reason == "unstated_filter"


@pytest.mark.parametrize(
    ("question", "value"),
    [
        ("What is the total budget of schools in district -2?", 2),  # sign
        ("What is the total budget of schools in district 2?", -2),
        ("What is the total budget of schools in district 1.5?", 5),  # tail of a decimal
        ("What is the total budget of schools in district 5.1?", 1.5),
        ("What is the total budget of schools in district 25?", 5),  # tail of a number
        ("What is the total budget of schools in district 2.5?", 5),
        ("What is the total budget of schools in district 5.0?", 5),
        ("What is the total budget of schools in district 5?", 5.0),
        ("What is the total budget of schools in Northern?", "North"),  # prefix of a word
        ("What is the total budget of schools in North?", "north"),  # case
        ("What is the total budget of schools in north-east?", "north"),  # glued
        ("What is the total budget of schools opened 2024-05-01?", "2024-01-05"),  # date order
        ("What is the total budget of schools opened 2024-01-05?", "2024-01-5"),
    ],
)
def test_mirror_the_literal_must_be_the_question_exactly(question: str, value: object) -> None:
    flt = ("bronze.public_schools", "district_id", "=", value)
    got = measure_fit(question, _pool(BUDGET), filters=[flt])
    assert got.ok is False, (question, value)


def test_mirror_a_short_literal_is_not_stated_by_the_tail_of_a_longer_one() -> None:
    q = "What is the total budget of schools in district 2.5?"
    both = ("bronze.public_schools", "district_id", "IN", [2.5, 5])
    got = measure_fit(q, _pool(BUDGET), filters=[both])
    assert (got.reason, got.detail) == ("unstated_filter", "'5'")


def test_mirror_a_threshold_or_a_row_window_is_a_condition_the_plan_states() -> None:
    q = "What is the total budget of schools?"
    assert measure_fit(q, _pool(BUDGET), filters=(), keep_gt=5.0).reason == "unstated_filter"
    assert measure_fit(q, _pool(BUDGET), filters=(), offset=3).reason == "unstated_filter"
    assert measure_fit(q, _pool(BUDGET), filters=(), keep_gt=None, offset=None).ok is True
    assert measure_fit(q, _pool(BUDGET), keep_gt=5.0).ok is True  # no plan, no accounting


def test_mirror_is_not_made_without_a_plan() -> None:
    q = "What is the total budget of schools in the north district?"
    assert measure_fit(q, _pool(BUDGET)).ok is True  # filters=None: no plan is being checked


# ----------------------------------------------- the frame, cue and vocabulary sets are closed


@pytest.mark.parametrize(
    "word", ["me", "us", "my", "we", "our", "i", "it", "its", "to", "be", "as", "or", "by", "per",
             "each", "every", "across", "where", "this", "that", "these", "those", "has", "have",
             "had", "will", "not", "but", "if", "then", "than"],
)  # fmt: skip
def test_the_pronouns_and_connectives_are_not_frame_words(word: str) -> None:
    q = f"What is the total budget of schools {word} schools?"
    assert _acct(q).reason == "unbound_constraint", word


@pytest.mark.parametrize("word", sorted(mu.FRAME_WORDS))
def test_a_frame_word_is_matched_lowercase_exactly(word: str) -> None:
    ok = f"What is the total budget of schools {word}"
    assert _acct(ok).ok, word
    for variant in (word.upper(), word.title() + "s", word + "s", word.title()):
        if variant == word:
            continue
        assert not _acct(f"What is the total budget of schools {variant}").ok, variant


def test_a_count_question_is_accounted_for_too() -> None:
    assert measure_fit("How many schools are there?", _pool(SCOUNT), filters=()).ok is True
    got = measure_fit("How many schools are there in the US?", _pool(SCOUNT), filters=())
    assert (got.reason, got.detail) == ("unbound_constraint", "'US'")


def test_the_first_token_alone_may_be_capitalised() -> None:
    assert _acct("What is the total budget of schools").ok
    assert not _acct("WHAT is the total budget of schools").ok
    assert not _acct("What is the Total budget of schools").ok
    assert not _acct("What is the total Budget of schools").ok
    assert not _acct("what IS the total budget of schools").ok
    # the capitalised first token is not a license to pluralise or fold
    assert _acct("Schools budget total").ok
    assert not _acct("School budget total").ok
    assert not _acct("Schoolss budget total").ok


def test_a_request_phrase_is_stripped_only_at_the_start_and_only_whole() -> None:
    assert _acct("show me the total budget of schools").ok
    assert _acct("Can you show me the total budget of schools").ok
    assert _acct("Please give me the total budget of schools").ok
    assert not _acct("the total budget of schools, show me").ok
    assert not _acct("What is the total budget of schools show me").ok
    assert not _acct("me the total budget of schools").ok
    assert not _acct("show the total budget of schools for me").ok


def test_a_cue_phrase_is_covered_only_whole() -> None:
    assert _acct("What is the number of schools budget").ok
    assert not _acct("What is the number schools budget").ok
    assert not _acct("What is the number budget of schools").ok
    assert _acct("How many schools budget").ok


def test_vocabulary_is_the_plans_own_names_and_one_plural_rule() -> None:
    assert _acct("total budgets of schools").ok  # budgets = budget + s
    assert _acct("total budget of schools").ok  # the grain table's name AT THE SOURCE
    # ... and not the landed relation's name: ``bronze.public_schools`` is table ``schools`` of
    # source schema ``public``, so "public schools" is a condition the plan does not carry
    public = _acct("total budget of public schools")
    assert (public.reason, public.unaccounted) == ("unbound_constraint", ("'public'",))
    assert not _acct("total budget of private schools").ok
    assert not _acct("total budget of schoolies").ok
    cat = mu.account_for_question(
        "total of categories", measure="category_sum", grain="bronze.category"
    )
    assert cat.ok  # y -> ies
    assert not mu.account_for_question(
        "total of Categories", measure="category_sum", grain="bronze.category"
    ).ok


def test_the_old_collision_cases_are_conditions_not_words() -> None:
    """Wills/Doe/Cans/Gets/Lists collided with stemmed frame words (will, do, can, get, list)."""
    for word in ("Wills", "Doe", "Cans", "Gets", "Lists", "Shows", "Gives", "Tells", "Finds"):
        assert _acct(f"What is the total budget of {word} schools").reason == (
            "unbound_constraint"
        ), word
        assert _acct(f"What is the total budget of schools {word}").reason == (
            "unbound_constraint"
        ), word


def test_fit_a_bool_literal_is_not_a_number_in_the_probe(env: _Env) -> None:
    """``TRUE = 1`` holds in SQL: a JSON ``true`` on an integer key must not filter to key 1."""
    onto = _lake_onto(env)
    stated = "How many schools are there in district 1?"
    assert filter_values_present(
        _plan((SCHOOLS, "district_id", "=", 1)), onto, env.lake, question=stated
    ) is None
    got = filter_values_present(_plan((SCHOOLS, "district_id", "=", True)), onto, env.lake)
    assert got is not None and got.startswith("unmatched_filter_value: "), got
    got = filter_values_present(
        _plan((SCHOOLS, "district_id", "IN", [1, True])),
        onto,
        env.lake,
        question="How many schools are there in district 1 and True?",
    )
    assert got is not None and "True" in got, got


def test_fit_by_the_measures_own_table_is_not_a_grouping_the_plan_computes() -> None:
    """A plan grouped by ``district_id`` of the schools table is "by district", never "by
    schools": the grain's table name does not name a breakdown."""
    grouped = [(SCHOOLS, "district_id")]
    ok = measure_fit(
        "What is the total budget of schools by district?",
        _pool(BUDGET),
        grouped_by=grouped,
        filters=(),
    )
    assert ok.ok is True
    bad = measure_fit(
        "What is the total budget of schools by schools?",
        _pool(BUDGET),
        grouped_by=grouped,
        filters=(),
    )
    assert (bad.ok, bad.reason) == (False, "unbound_constraint")
    assert bad.detail == "'by'"
