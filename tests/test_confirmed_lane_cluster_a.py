"""The confirmed lane, cluster A of the adversary rounds (dms#284): a plausible figure under the
confirmed badge that answers a different question. Every case posts ``POST /v1/chat/ask``, runs
``assert_envelope_valid`` (through the rig) and asserts the badge, ``abstained``, the rendered text
and the returned rows against an independent oracle query on the lake, never the SQL alone
(CLAUDE.md rules 10, 10a).

* A1  two stated values on DIFFERENT columns AND to the empty set: ``COUNT`` printed 0 under the
      badge. Closed at the root in two layers that share no word list: (i) a plan with two or
      more filters on different columns does not read ``and`` as a harmless frame word, and (ii) a
      confirmed answer computed under filters that match NO row of the grain abstains
      ``empty_aggregate``, however the question was phrased.
* A4  rule 12: tab, newline, zero-width, NBSP and Unicode-normalisation variants of a stored
      value are other spellings, so the equality that drops them is refused.
* A5  a registry row whose ``truncated`` is NULL (unknown) does not show the source whole.
* A6  a stated number binds only to a column a word of the question stands beside.
* A7  a COUNT measure states a number the question ASKED for, never the rows ("show me").
* A8  a figure PII masking turns into a token is not stated (``masked_figure``).
* A9  the lane reads at most 500 characters; its cost is linear in the question.

A2 (a route among several) and A3 (a plan limit) are pinned in tests/test_confirmed_lane_guards.py.
"""

from __future__ import annotations

import copy
import time
import unicodedata
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import generative_ask as ga
from dms_executor import measure_use as mu
from dms_executor.space_ontology import set_ontology_store
from test_confirmed_lane_guards import (
    BUDGET,
    DISTRICTS,
    SCHOOLS,
    SCOUNT,
    TOTAL_Q,
    _lake,
    build,
    ranked,
)
from test_connect_ask_02 import _assert_answer, _reasons, _Rigged
from test_ingest_fixes_277 import _oracle
from test_onto_confirm_01 import _lake_exec
from test_onto_derive_01 import _assert_abstain, make_minter


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
def r(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> _Rigged:
    return build(tmp_path, monkeypatch, minter, store)


NORTH_AND_SOUTH = ((DISTRICTS, "name", "=", "North"), (SCHOOLS, "region", "=", "South"))
SOUTH_REGION_Q = "What is the total budget of schools in region South?"


def _count(rigged: _Rigged, where: str = "TRUE") -> list[dict[str, Any]]:
    return _oracle(
        rigged.rig.lake, f"SELECT COUNT(*) AS v FROM bronze.public_schools WHERE {where}"
    )


def _abstain_on(env: dict[str, Any], *gaps: str) -> str:
    """An abstain (no rows, no values, no badge but ABSTAIN) that names one of ``gaps``."""
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True, (env["badge"], env.get("text"))
    assert env["rows"] == [] and env["values"] == [], env
    text = str(env.get("text") or "")
    assert any(f"gap: {g}" in text for g in gaps), text
    return text


def _no_figure(env: dict[str, Any], rigged: _Rigged) -> str:
    """A named abstain: no rows, no values, no basis, no figure in the sentence."""
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["rows"] == [] and env["values"] == [] and "measure_basis" not in env
    return str(env["text"])


# ============================================================ A1: a cross-column AND


AND_SHAPES = [
    "How many schools in North and schools in South?",
    "How many schools are there in North and schools in South?",
    "How many schools are there in North and how many schools in South?",
    "How many schools are in North and the schools in South?",
    "How many schools in North and the schools in South?",
]

#: Shapes with no ``and`` at all. No word of any list is needed for them: the plan's conjunction
#: matches no school, and a confirmed answer over no rows is not a figure.
NO_AND_SHAPES = [
    "How many schools in North, schools in South?",
    "How many schools are there in North South?",
    "How many schools are there in North with South?",
    "How many schools are there in North in South?",
    "How many schools are there in North for South?",
    "How many schools are there in North of South?",
    "How many schools are there in North at South?",
    "How many schools are there in North from South?",
    "How many schools are there in the North the South?",
    "How many schools are there in North. South?",
    "How many schools are there in all North South?",
    "How many schools are there in North all South?",
]


@pytest.mark.parametrize("question", AND_SHAPES)
def test_a1_i_and_between_two_conditions_is_not_a_frame_word(r: _Rigged, question: str) -> None:
    """Layer (i). ``and`` is a word the plan does not represent when it filters two columns."""
    assert _count(r, "district_id = 1 AND region = 'South'") == [{"v": 0}]
    env = r.plan(question, SCOUNT, filters=NORTH_AND_SOUTH)
    text = _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:unbound_constraint" in _reasons(env) and "'and'" in _reasons(env)
    assert "schools_count=0" not in text and "measure_basis" not in env
    assert r.executed == []


@pytest.mark.parametrize("question", NO_AND_SHAPES)
def test_a1_ii_a_conjunction_that_matches_no_row_is_not_a_zero(r: _Rigged, question: str) -> None:
    """Layer (ii). The plan ANDs a district and a region that no school shares: the lane would
    print COUNT 0 under the badge. It abstains, whatever the question's words."""
    assert _count(r, "district_id = 1 AND region = 'South'") == [{"v": 0}]
    env = r.plan(question, SCOUNT, filters=NORTH_AND_SOUTH)
    # A bare value that another column holds too ('North' is a region as well) is refused by
    # the binding rule before the emptiness is counted: either named abstain is the answer.
    text = _abstain_on(env, "empty_aggregate", "ambiguous_filter_value")
    assert "schools_count=0" not in text and "Found 1 row" not in text
    assert _no_figure(env, r) == text
    assert "measure_basis" not in env and r.executed == []


def test_a1_ii_holds_for_the_ranking_route_and_for_a_sum_too(r: _Rigged) -> None:
    env = r.plan(
        "What is the total budget of schools in North South?",
        BUDGET,
        filters=NORTH_AND_SOUTH,
    )
    text = _abstain_on(env, "empty_aggregate", "ambiguous_filter_value")
    assert "None" not in text and "measure_basis" not in env


def test_a1_ii_the_empty_conjunction_itself_is_refused_once_both_columns_are_named(
    r: _Rigged,
) -> None:
    """Layer (ii) alone: both values are bound to their columns by the question, so the only
    reason left is that no school is in district North AND region South: COUNT 0 is refused."""
    assert _count(r, "district_id = 1 AND region = 'South'") == [{"v": 0}]
    env = r.plan(
        "How many schools are there in district name North and region South?",
        SCOUNT,
        filters=NORTH_AND_SOUTH,
    )
    text = _assert_abstain(env, "empty_aggregate")
    assert "schools_count=0" not in text and env["rows"] == []
    assert r.executed == []


def test_a1_ii_a_legitimate_zero_under_a_filter_is_refused_not_stated(r: _Rigged) -> None:
    """The stated cost of layer (ii): no school is both South and private, the question is
    well formed, and the answer is "none". The lane does not print 0 under a certified badge for
    it (a zero over the filtered set cannot be told from a filter that matched nothing)."""
    assert _count(r, "district_id = 2 AND kind = 'private'") == [{"v": 0}]
    env = r.plan(
        "How many private schools are there in district name South?",
        SCOUNT,
        filters=((SCHOOLS, "kind", "=", "private"), (DISTRICTS, "name", "=", "South")),
    )
    _assert_abstain(env, "empty_aggregate")
    assert env["rows"] == []


@pytest.mark.parametrize(
    ("question", "filters", "where", "figure"),
    [
        (
            "How many schools are there in North and South?",
            ((DISTRICTS, "name", "IN", ["North", "South"]),),
            "district_id IN (1, 2)",
            "4",
        ),
        (
            "How many public schools are there in district name North and South?",
            ((SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "IN", ["North", "South"])),
            "kind = 'public' AND district_id IN (1, 2)",
            "2",
        ),
        (
            "How many public schools are there in district name North?",
            ((SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "=", "North")),
            "kind = 'public' AND district_id = 1",
            "1",
        ),
        (
            "How many public schools are there in region South?",
            ((SCHOOLS, "kind", "=", "public"), (SCHOOLS, "region", "=", "South")),
            "kind = 'public' AND region = 'South'",
            "1",
        ),
    ],
)
def test_a1_controls_what_matches_rows_still_answers(
    r: _Rigged,
    question: str,
    filters: tuple[tuple[Any, ...], ...],
    where: str,
    figure: str,
) -> None:
    oracle = _count(r, where)
    assert oracle[0]["v"] > 0
    _assert_answer(r.plan(question, SCOUNT, filters=filters), oracle, figure)


def test_a1_an_unfiltered_count_is_never_refused_by_the_empty_rule(r: _Rigged) -> None:
    _assert_answer(r.plan("How many schools are there?", SCOUNT), _count(r), "4")
    _lake_exec(r.rig.lake, "DELETE FROM bronze.public_schools")
    env = r.plan("How many schools are there?", SCOUNT)
    _assert_answer(env, _count(r), "0")  # a real zero: no filter, so nothing was dropped


def test_a1_unit_and_is_a_frame_word_only_for_a_plan_with_one_column() -> None:
    acc = mu.account_for_question
    grain = SCHOOLS
    two = [(DISTRICTS, "name", "=", "North"), (SCHOOLS, "region", "=", "South")]
    q = "how many schools in North and schools in South"
    got = acc(q, measure=SCOUNT, grain=grain, filters=two)
    assert (got.ok, got.reason) == (False, "unbound_constraint"), got
    assert "'and'" in got.unaccounted
    one = [(DISTRICTS, "name", "IN", ["North", "South"])]
    assert acc("how many schools in North and South", measure=SCOUNT, grain=grain, filters=one).ok
    mixed = [(SCHOOLS, "kind", "=", "public"), (DISTRICTS, "name", "IN", ["North", "South"])]
    assert acc(
        "how many public schools in North and South", measure=SCOUNT, grain=grain, filters=mixed
    ).ok


def test_v10_f3_a_second_subject_joined_by_and_is_not_covered_by_the_filter_table_name(
    r: _Rigged,
) -> None:
    """Step-10 verifier F3: "schools and districts" asks for two things; the plan's filter table
    name made "districts" a covered word, and "and" a frame word, so the schools total answered."""
    north = ((DISTRICTS, "name", "=", "North"),)
    env = r.plan(
        "What is the total budget of schools and districts in the North district?", BUDGET,
        filters=north,
    )  # fmt: skip
    text = _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:unbound_constraint" in _reasons(env) and "'and'" in _reasons(env)
    assert "310.75" not in text and r.executed == []
    oracle = _oracle(
        r.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools WHERE district_id = 1"
    )
    _assert_answer(
        r.plan("What is the total budget of schools in the North district?", BUDGET, filters=north),
        oracle,
        "310.75",
    )


def test_v10_f2_a_plan_that_groups_by_more_than_the_question_names_is_refused(
    r: _Rigged,
) -> None:
    """Step-10 verifier F2: "by district" answered with a district x school-name breakdown."""
    env = r.plan(
        "What is the total budget of schools by district?", BUDGET,
        group_by=((SCHOOLS, "district_id"), (SCHOOLS, "name")),
    )  # fmt: skip
    text = _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:unbound_constraint" in _reasons(env) and "'name'" in _reasons(env)
    assert "Alder" not in text and r.executed == []
    # what the question names answers: one dimension, and two when it lists both
    by_district = _oracle(
        r.rig.lake,
        "SELECT district_id, SUM(budget) AS v FROM bronze.public_schools GROUP BY district_id",
    )
    env = r.plan(
        "What is the total budget of schools by district?", BUDGET,
        group_by=((SCHOOLS, "district_id"),),
    )  # fmt: skip
    _assert_answer(env, by_district, "Found 2 row(s).")
    both = _oracle(
        r.rig.lake,
        "SELECT district_id, name, SUM(budget) AS v FROM bronze.public_schools "
        "GROUP BY district_id, name",
    )
    env = r.plan(
        "What is the total budget of schools by district and name?", BUDGET,
        group_by=((SCHOOLS, "district_id"), (SCHOOLS, "name")),
    )  # fmt: skip
    _assert_answer(env, both, "Found 4 row(s).")


# ============================================================ A4: other spellings of a value

VARIANTS = [
    "South\t",
    "South\n",
    "South\r\n",
    "South​",
    "South ",
    " South",
    "South﻿",
    "S̶outh",
    "SOUTH ",
    "Sóuth",
]


def _with_region(variant: str, *, keep_exact: bool) -> dict[str, Any]:
    held = copy.deepcopy(_lake())
    rows = held["data"]['"public"."schools"'][1]
    if keep_exact:
        rows.append([5, 2, "Elm", Decimal("1000.00"), "public", variant])
    else:
        rows[2][5] = variant  # Cedar is the only South school, and only the variant remains
    return held


@pytest.mark.parametrize("variant", VARIANTS)
def test_a4_a_column_that_also_holds_another_spelling_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore, variant: str
) -> None:
    rigged = build(
        tmp_path, monkeypatch, minter, store, lake=_with_region(variant, keep_exact=True)
    )
    exact = _oracle(
        rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools WHERE region = 'South'"
    )
    assert exact == [{"v": Decimal("50.00")}]  # the figure the equality would state
    env = rigged.plan(SOUTH_REGION_Q, BUDGET, filters=((SCHOOLS, "region", "=", "South"),))
    _assert_abstain(env, "ambiguous_filter_value")
    assert repr(variant) in _reasons(env), _reasons(env)
    assert "measure_basis" not in env and rigged.executed == []


@pytest.mark.parametrize("variant", VARIANTS)
def test_a4_a_column_that_holds_only_another_spelling_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore, variant: str
) -> None:
    rigged = build(
        tmp_path, monkeypatch, minter, store, lake=_with_region(variant, keep_exact=False)
    )
    env = rigged.plan(SOUTH_REGION_Q, BUDGET, filters=((SCHOOLS, "region", "=", "South"),))
    _assert_abstain(env, "unmatched_filter_value")
    assert repr(variant) in _reasons(env), _reasons(env)
    assert rigged.executed == []


def test_a4_nfc_and_nfd_are_two_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    nfc = "Zürich"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    held = copy.deepcopy(_lake())
    rows = held["data"]['"public"."schools"'][1]
    rows[0][5] = nfc
    rows.append([5, 2, "Elm", Decimal("1000.00"), "public", nfd])
    rigged = build(tmp_path, monkeypatch, minter, store, lake=held)
    env = rigged.plan(
        "What is the total budget of schools in region Zürich?",
        BUDGET,
        filters=((SCHOOLS, "region", "=", nfc),),
    )
    _assert_abstain(env, "ambiguous_filter_value")
    assert repr(nfd) in _reasons(env), _reasons(env)


def test_a4_control_a_clean_column_still_answers_and_a_real_other_value_is_not_a_variant(
    r: _Rigged,
) -> None:
    env = r.plan(SOUTH_REGION_Q, BUDGET, filters=((SCHOOLS, "region", "=", "South"),))
    _assert_answer(env, [{"v": Decimal("50.00")}], "50")
    # 'North' also exists as 'north' in this lake: that was already ambiguous, and still is
    env = r.plan(
        "What is the total budget of schools in region North?",
        BUDGET,
        filters=((SCHOOLS, "region", "=", "North"),),
    )
    _assert_abstain(env, "ambiguous_filter_value")


# ============================================================ A5: truncated unknown


def test_a5_a_registry_row_with_no_truncated_flag_does_not_show_the_source_whole(
    r: _Rigged,
) -> None:
    _assert_answer(r.plan(TOTAL_Q, BUDGET), [{"v": Decimal("360.75")}], "360.75")
    _lake_exec(r.rig.lake, "UPDATE bronze._ingest_registry SET truncated = NULL")
    r.rig.cortex.executed.clear()
    env = r.plan(TOTAL_Q, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert "360.75" not in text and "measure_basis" not in env
    assert f"{BUDGET} is not usable now: source_truncated" in _reasons(env), _reasons(env)
    assert r.executed == []
    env = ranked(r, TOTAL_Q)
    assert env["abstained"] is True and env["rows"] == [] and "360.75" not in str(env["text"])
    # an explicit FALSE (a whole pull) is what answers
    _lake_exec(r.rig.lake, "UPDATE bronze._ingest_registry SET truncated = FALSE")
    _assert_answer(r.plan(TOTAL_Q, BUDGET), [{"v": Decimal("360.75")}], "360.75")


def test_a5_unit_the_pull_record_keeps_unknown_as_unknown(r: _Rigged) -> None:
    _lake_exec(r.rig.lake, "UPDATE bronze._ingest_registry SET truncated = NULL")
    pulls = mu.read_source_pulls(r.rig.lake, [SCHOOLS])
    assert pulls is not None and pulls[SCHOOLS].truncated is None


# ============================================================ A6: a number binds to its column


def test_a6_two_schools_is_not_school_id_2(r: _Rigged) -> None:
    assert _oracle(r.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools") == [
        {"v": Decimal("360.75")}
    ]
    env = r.plan(
        "What is the total budget of 2 schools?", BUDGET, filters=((SCHOOLS, "school_id", "=", 2),)
    )
    text = _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:unbound_constraint" in _reasons(env) and "'2'" in _reasons(env)
    assert "200.25" not in text and "measure_basis" not in env and r.executed == []


@pytest.mark.parametrize(
    ("question", "filters", "where", "figure"),
    [
        (
            "What is the total budget of school id 2?",
            ((SCHOOLS, "school_id", "=", 2),),
            "school_id = 2",
            "200.25",
        ),
        (
            "What is the total budget of school 2?",
            ((SCHOOLS, "school_id", "=", 2),),
            "school_id = 2",
            "200.25",
        ),
        (
            "What is the total budget of schools in district 2?",
            ((SCHOOLS, "district_id", "=", 2),),
            "district_id = 2",
            "50",
        ),
        (
            "What is the total budget of schools in districts 1 and 2?",
            ((SCHOOLS, "district_id", "IN", [1, 2]),),
            "district_id IN (1, 2)",
            "360.75",
        ),
    ],
)
def test_a6_controls_a_number_beside_its_own_column_answers(
    r: _Rigged, question: str, filters: tuple[tuple[Any, ...], ...], where: str, figure: str
) -> None:
    oracle = _oracle(
        r.rig.lake, f"SELECT SUM(budget) AS v FROM bronze.public_schools WHERE {where}"
    )
    _assert_answer(r.plan(question, BUDGET, filters=filters), oracle, figure)


@pytest.mark.parametrize(
    "question",
    [
        "What is the total budget of schools with 2?",
        "What is the total budget of the 2 schools in district?",
        "What is the total budget of 2 districts?",
    ],
)
def test_a6_a_number_beside_another_word_does_not_bind(r: _Rigged, question: str) -> None:
    env = r.plan(question, BUDGET, filters=((SCHOOLS, "district_id", "=", 2),))
    _assert_abstain(env, "no_declared_measure")
    assert r.executed == []


def test_a6_unit_the_binding_is_by_position() -> None:
    acc = mu.account_for_question
    f = [(SCHOOLS, "district_id", "=", 2)]
    assert acc("total budget of schools in district 2", measure=BUDGET, grain=SCHOOLS, filters=f).ok
    got = acc("total budget of 2 schools", measure=BUDGET, grain=SCHOOLS, filters=f)
    assert (got.ok, got.reason) == (False, "unbound_constraint"), got
    # a text value that happens to be digits is not a number the question must bind
    t = [(SCHOOLS, "region", "=", "2024")]
    assert acc("total budget of schools in 2024", measure=BUDGET, grain=SCHOOLS, filters=t).ok


# ============================================================ A7: a count states a count


@pytest.mark.parametrize(
    "question",
    [
        "Show me the schools",
        "What are the schools?",
        "Find the schools",
        "List the schools",
        "Give me the schools",
        "Tell me the schools",
    ],
)
def test_a7_a_listing_question_is_not_answered_by_a_count(r: _Rigged, question: str) -> None:
    env = ranked(r, question, SCOUNT)
    text = str(env["text"])
    assert env["abstained"] is True and env["rows"] == [] and "schools_count" not in text
    assert "measure_basis" not in env
    env = r.plan(question, SCOUNT)
    _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:aggregate_cue" in _reasons(env) and r.executed == []


@pytest.mark.parametrize(
    "question",
    [
        "How many schools are there?",
        "What is the number of schools?",
        "Show me how many schools there are",
        "Give me the count of schools",
        "What is the total number of schools?",
    ],
)
def test_a7_a_count_cue_answers_with_the_count(r: _Rigged, question: str) -> None:
    _assert_answer(r.plan(question, SCOUNT), _count(r), "4")


# ============================================================ A8: a masked figure


def _big_lake() -> tuple[dict[str, Any], str]:
    held = copy.deepcopy(_lake())
    held["data"]['"public"."schools"'][1][0][3] = Decimal("9007199254740993.00")
    held["types"]["public.schools"][3] = ("budget", "numeric", 20, 2)
    return held, "9007199254740993"


def test_a8_a_total_the_masker_would_tokenise_is_not_stated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    held, digits = _big_lake()
    rigged = build(tmp_path, monkeypatch, minter, store, lake=held)
    truth = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    assert str(truth[0]["v"]).startswith("9007199254741")  # the figure exists
    env = rigged.plan(TOTAL_Q, BUDGET)
    text = _assert_abstain(env, "masked_figure")
    assert "DMSMASK" not in str(env) and digits not in text and "measure_basis" not in env
    assert env["rows"] == []
    assert BUDGET in _reasons(env)


def test_a8_a_small_total_and_a_group_key_that_is_masked_still_answer(r: _Rigged) -> None:
    _assert_answer(r.plan(TOTAL_Q, BUDGET), [{"v": Decimal("360.75")}], "360.75")


def test_a8_unit_only_a_figure_cell_is_judged() -> None:
    # the masker reads a Luhn-valid digit run as an account number; the Alder-edited total is one
    assert ga._figure_masked([{"b": Decimal("9007199254741253.25")}], "b") is True
    assert ga._figure_masked([{"b": 4111111111111111}], "b") is True
    assert ga._figure_masked([{"b": Decimal("1234567812345678")}], "b") is False  # not hidden
    assert ga._figure_masked([{"b": Decimal("360.75")}], "b") is False
    assert ga._figure_masked([{"b": 12, "name": "Alder"}], "b") is False


# ============================================================ A9: a bounded, linear lane


def test_a9_the_lane_reads_at_most_500_characters(r: _Rigged) -> None:
    pad = "x" * (501 - len("What is the total budget of all schools?"))
    long_q = "What is the total budget of all schools?" + pad
    assert len(long_q) == 501
    env = r.plan(long_q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:question_too_long" in _reasons(env) and r.executed == []
    env = ranked(r, long_q)
    assert env["abstained"] is True and "360.75" not in str(env["text"])
    # the boundary: 500 characters is read (and refused here only for its unknown word)
    just = mu.measure_fit("What is the total budget of all schools?" + "x" * 460, {}, filters=[])
    assert just.reason == "no_match"
    over = mu.measure_fit("x" * 501, {}, filters=[])
    assert over.reason == "question_too_long" and over.ok is False


def test_a9_account_for_question_is_linear_in_the_question() -> None:
    base = "What is the total budget of schools in North "
    filters = [(SCHOOLS, "name", "=", "a"), (DISTRICTS, "name", "=", "North")]
    for question in (
        base + "a " * 50_000,
        base + "North " * 17_000,
        base + "a" * 100_000,
        base + "total number " * 8_000,
    ):
        assert len(question) >= 100_000
        t0 = time.perf_counter()
        mu.account_for_question(question, measure=BUDGET, grain=SCHOOLS, filters=filters)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, (elapsed, question[:60])


def test_a9_many_stated_values_do_not_make_the_claim_quadratic() -> None:
    """Every stated value is a claimed span; testing each new one against all the earlier ones
    is quadratic. 12,000 values, one IN list, a 70 KB question."""
    values = list(range(10_000, 22_000))
    question = "What is the total budget of schools in district " + " ".join(map(str, values))
    assert len(question) > 60_000
    t0 = time.perf_counter()
    got = mu.account_for_question(
        question, measure=BUDGET, grain=SCHOOLS, filters=[(SCHOOLS, "district_id", "IN", values)]
    )
    elapsed = time.perf_counter() - t0
    assert got.ok is True, got
    assert elapsed < 0.8, elapsed  # ~0.15 s linear; the pairwise scan took 1.6 s


def test_a9_a_100k_question_at_the_envelope_is_answered_fast(r: _Rigged) -> None:
    question = "What is the total budget of schools in North " + "a " * 50_000
    t0 = time.perf_counter()
    env = r.plan(question, BUDGET, filters=((DISTRICTS, "name", "=", "North"),))
    elapsed = time.perf_counter() - t0
    assert env["abstained"] is True and "measure_basis" not in env and r.executed == []
    assert elapsed < 1.0, elapsed


def test_a8_a_masked_figure_is_refused_before_the_envelope_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    """The first layer sits before the ledger append: a figure the privacy layer would rewrite
    never reaches the envelope stage, so no ledger entry is written for it."""
    held, _digits = _big_lake()
    rigged = build(tmp_path, monkeypatch, minter, store, lake=held)

    def _reached(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a masked figure reached the envelope stage")

    monkeypatch.setattr(ga, "_l2_envelope", _reached)
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "masked_figure")
    assert env["rows"] == [] and "measure_basis" not in env


def test_a8_the_envelope_backstop_refuses_a_token_the_first_check_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    """The second layer: with the pre-ledger check switched off, the masked token the envelope
    would carry is still refused, never stated as the figure."""
    held, digits = _big_lake()
    rigged = build(tmp_path, monkeypatch, minter, store, lake=held)
    monkeypatch.setattr(ga, "_figure_masked", lambda *_a, **_k: False)
    env = rigged.plan(TOTAL_Q, BUDGET)
    text = _assert_abstain(env, "masked_figure")
    assert "DMSMASK" not in str(env) and digits not in text
