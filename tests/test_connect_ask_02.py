"""CONNECT-ASK-02 (dms#284) at the customer envelope: confirmed measures on the ask path.

Steps 6 and 7 of the measure lane (load, prune, wire budget; the two named abstains and their
plain sentences). Every case posts ``POST /v1/chat/ask``,
runs ``assert_envelope_valid`` and asserts the badge, ``abstained``, the rendered text and the
returned rows against an independent oracle query on the lake, never the SQL alone
(CLAUDE.md rules 10, 10a). The Space is the typed schools data of tests/test_ingest_fixes_277.py
(4 schools, total budget 360.75, North 310.75, South 50.00); the measures are the proposer's,
confirmed through the real steward service.

The badge a confirmed-measure answer wears is ``CONFIRMED_MEASURE_BADGE`` (``L2_VALIDATED`` until
the founder-gated flip). No new badge value exists yet, so none is asserted here.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import test_ingest_fixes_277 as ingest277
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_store import OntologyStore, measure_definition_hash
from dms_core.measure_line import confirmed_line
from dms_executor.gen_path_refuse import GAP_REASONS, customer_abstain_text, gap_reason_name
from dms_executor.measure_basis import (
    CONFIRMED_BASIS_KEYS,
    CONFIRMED_MEASURE_BADGE,
    CONFIRMED_MEMORY_ONLY_SUFFIX,
    CONFIRMED_PREFIX,
)
from dms_executor.space_ontology import relabel_join_gap, set_ontology_store
from test_ingest_fixes_277 import _oracle, _Rig, _types
from test_onto_confirm_01 import SCHOOLS, _Env, _lake_exec, _Ledger
from test_onto_derive_01 import _assert_abstain, _reasons, _Space, _space, make_minter

DISTRICTS = "bronze.public_districts"
TOTAL_Q = "What is the total budget of all schools?"
COUNT_Q = "How many schools are there?"
BUDGET = "schools_budget_sum"
COUNT = "schools_count"
SUM_SQL = "SELECT SUM(s.budget) AS total_budget FROM bronze.public_schools s"
WRONG_SQL = "SELECT 1 AS total_budget"


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


class _Rigged:
    """A Space with the proposer's measures proposed, and helpers to confirm and ask."""

    def __init__(self, env: _Env) -> None:
        self.env = env
        self.rig = env.rig
        self.views = env.proposed()
        self.space = _Space(env.rig.client, env.rig.cortex, env.space, env.lake)
        cortex = env.rig.cortex
        real_ask = cortex.ask

        def recording_ask(req: Any) -> Any:  # the contract ask is a fallback these cases forbid
            cortex.asks.append(req)
            return real_ask(req)

        cortex.ask = recording_ask  # type: ignore[method-assign]

    def confirm(self, *names: str) -> None:
        for name in names:
            self.env.confirm(self.views[name])

    def row_id(self, name: str) -> str:
        return self.views[name]["id"]

    def plan(
        self,
        question: str,
        measure: str,
        *,
        group_by: tuple[tuple[str, str], ...] = (),
        filters: tuple[tuple[str, str, str, Any], ...] = (),
        query_sql: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query_plan": {
                "measure": measure,
                "group_by": [list(g) for g in group_by],
                "filters": [list(f) for f in filters],
            },
            "plan_source": "ontology_plan",
        }
        if query_sql is not None:
            payload["query_sql"] = query_sql
        return self.space.ask(question, payload)

    def sql(self, question: str, sql: str) -> dict[str, Any]:
        return self.space.ask(question, {"query_sql": sql, "plan_source": "ontology_plan"})

    def ranked(self, question: str, measure: str, query_sql: str) -> dict[str, Any]:
        """Cortex returned valid SQL AND ranked ``measure`` first: the router's choice."""
        return self.space.ask(
            question,
            {
                "query_sql": query_sql,
                "plan_source": "ontology_plan",
                "ontology": {"metrics": [{"id": measure}]},
            },
        )

    @property
    def executed(self) -> list[str]:
        return list(self.rig.cortex.executed)

    @property
    def wire_measures(self) -> dict[str, Any]:
        return dict(self.rig.cortex.insights[-1]["ontology"]["measures"])

    def probe_sql(self, name: str) -> str:
        return str(self.env.row(self.row_id(name)).evidence["probe_sql"])


@pytest.fixture()
def rigged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> _Rigged:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Connect ask 02")
    rig.ingest(space)
    env = _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())
    return _Rigged(env)


def _norm(value: Any) -> str:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except ArithmeticError:
        return str(value)


def _multiset(rows: list[dict[str, Any]]) -> Counter[tuple[str, ...]]:
    """Rows compared by value, numbers as numbers (360.75 == Decimal('360.75'), 370.0 == 370.00)."""
    return Counter(tuple(sorted(_norm(v) for v in row.values())) for row in rows)


def _assert_answer(env: dict[str, Any], rows: list[dict[str, Any]], figure: str) -> None:
    assert env["badge"] == CONFIRMED_MEASURE_BADGE, (
        env["badge"], env.get("text"), env.get("assumptions"),
    )  # fmt: skip
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset(rows)
    assert figure in str(env.get("text") or ""), env.get("text")
    assert env.get("audit_id"), "an answer carries the ledger entry that produced it"


# ---------------------------------------------------------------- nothing confirmed: inert


def test_a_proposed_measure_never_answers_and_never_reaches_the_wire(rigged: _Rigged) -> None:
    """E02. Proposals exist; nothing is confirmed, so the wire carries no measures (the
    pre-existing ``sent['measures'] == {}``) and a plan naming one is a named abstain."""
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert rigged.wire_measures == {}
    assert rigged.executed == []
    assert rigged.rig.cortex.asks == [], "no contract-ask fallback after a named abstain"


def test_a_sql_answer_is_unchanged_when_nothing_is_confirmed(rigged: _Rigged) -> None:
    env = rigged.sql(TOTAL_Q, SUM_SQL)
    oracle = _oracle(rigged.rig.lake, SUM_SQL)
    assert oracle == [{"total_budget": Decimal("360.75")}]
    _assert_answer(env, oracle, "360.75")
    assert rigged.executed == [SUM_SQL]
    assert rigged.wire_measures == {}


# ---------------------------------------------------------------- confirmed: loaded and used


def test_a_confirmed_measure_reaches_the_wire_and_answers_with_oracle_rows(
    rigged: _Rigged,
) -> None:
    """E01 as far as step 6 goes. The plan names the confirmed measure and Cortex also sent a
    different, wrong ``query_sql``: the typed plan wins, DMS compiles it, and the figure is the
    measure's, not the SQL's."""
    rigged.confirm(BUDGET)
    env = rigged.plan(TOTAL_Q, BUDGET, query_sql=WRONG_SQL)

    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    assert oracle == [{"v": Decimal("360.75")}]
    _assert_answer(env, oracle, "360.75")
    # The only SQL that reached the lake is the compile of the confirmed definition: the very
    # statement the steward saw as evidence when confirming.
    assert rigged.executed == [rigged.probe_sql(BUDGET)]
    assert WRONG_SQL not in rigged.executed

    # The wire: grain + description per measure, never an expression.
    row = rigged.env.row(rigged.row_id(BUDGET))
    assert rigged.wire_measures == {BUDGET: {"grain": SCHOOLS, "description": row.description}}
    wire = json.dumps(rigged.rig.cortex.insights[-1]["ontology"])
    assert "expression" not in wire and "SUM(" not in wire and "COUNT(" not in wire


def test_a_count_measure_answers_with_the_star_column(rigged: _Rigged) -> None:
    rigged.confirm(COUNT)
    env = rigged.plan(COUNT_Q, COUNT)
    oracle = _oracle(rigged.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")
    assert oracle == [{"v": 4}]
    _assert_answer(env, oracle, "4")
    assert rigged.executed == [rigged.probe_sql(COUNT)]


def test_a_confirmed_measure_does_not_disturb_a_generated_sql_answer(rigged: _Rigged) -> None:
    """Ad hoc generated SQL keeps today's L2_VALIDATED path whether or not a measure exists."""
    rigged.confirm(BUDGET)
    env = rigged.sql(TOTAL_Q, SUM_SQL)
    oracle = _oracle(rigged.rig.lake, SUM_SQL)
    _assert_answer(env, oracle, "360.75")
    assert env["badge"] == "L2_VALIDATED"
    assert rigged.executed == [SUM_SQL]
    assert BUDGET in rigged.wire_measures


def test_a_confirmed_measure_filtered_through_a_verified_link_answers(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    q = "What is the total budget of schools in the North district?"
    env = rigged.plan(q, BUDGET, filters=((DISTRICTS, "name", "=", "North"),))
    oracle = _oracle(
        rigged.rig.lake,
        "SELECT SUM(s.budget) AS v FROM bronze.public_schools s "
        "JOIN bronze.public_districts d ON s.district_id = d.district_id WHERE d.name = 'North'",
    )
    assert oracle == [{"v": Decimal("310.75")}]
    _assert_answer(env, oracle, "310.75")


# ---------------------------------------------------------------- reject, withdraw, confirm again


def test_a_rejected_or_withdrawn_measure_stops_answering_on_the_next_ask(
    rigged: _Rigged,
) -> None:
    """E03."""
    rigged.confirm(BUDGET)
    first = rigged.plan(TOTAL_Q, BUDGET)
    _assert_answer(first, _oracle(rigged.rig.lake, SUM_SQL), "360.75")
    assert len(rigged.executed) == 1

    rigged.env.reject(rigged.env.row(rigged.row_id(BUDGET)), "withdrawn: wrong definition")
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert rigged.wire_measures == {} and len(rigged.executed) == 1  # nothing new ran

    # proposing again does not resurrect it
    assert BUDGET not in {m["name"] for m in rigged.env.propose()["created"]}
    again = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(again, "no_declared_measure")
    assert len(rigged.executed) == 1

    # confirming the rejected row again re-runs every criterion and answers again
    rigged.env.confirm(rigged.env.row(rigged.row_id(BUDGET)))
    back = rigged.plan(TOTAL_Q, BUDGET)
    _assert_answer(back, _oracle(rigged.rig.lake, SUM_SQL), "360.75")
    assert len(rigged.executed) == 2 and BUDGET in rigged.wire_measures


# ---------------------------------------------------------------- the lake moved (E11)


@pytest.mark.parametrize(
    "ddl",
    [
        "ALTER TABLE bronze.public_schools DROP COLUMN budget",
        "ALTER TABLE bronze.public_schools RENAME COLUMN budget TO budget_myr",
        "ALTER TABLE bronze.public_schools ALTER budget TYPE VARCHAR",
        "ALTER TABLE bronze.public_schools ALTER budget TYPE DOUBLE",
    ],
)
def test_a_column_that_moved_abstains_and_the_other_measures_keep_answering(
    rigged: _Rigged, ddl: str
) -> None:
    rigged.confirm(BUDGET, COUNT)
    _lake_exec(rigged.rig.lake, ddl)

    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert rigged.executed == [], "the measure over a changed column reached the lake"
    assert BUDGET not in rigged.wire_measures  # pruned before Cortex was asked
    assert COUNT in rigged.wire_measures

    # a measure that does not depend on the column is untouched, not over-abstained
    ok = rigged.plan(COUNT_Q, COUNT)
    oracle = _oracle(rigged.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")
    _assert_answer(ok, oracle, "4")


def test_a_grain_whose_key_stopped_being_unique_abstains(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET, COUNT, "districts_count")
    _lake_exec(
        rigged.rig.lake,
        "INSERT INTO bronze.public_schools SELECT * FROM bronze.public_schools WHERE school_id = 1",
    )
    for q, name in ((TOTAL_Q, BUDGET), (COUNT_Q, COUNT)):
        env = rigged.plan(q, name)
        _assert_abstain(env, "no_declared_measure")
    assert rigged.executed == [] and set(rigged.wire_measures) == {"districts_count"}

    # the other grain still verifies, so its measure still answers
    ok = rigged.plan("How many districts are there?", "districts_count")
    _assert_answer(
        ok, _oracle(rigged.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_districts"), "2"
    )


def test_an_orphan_foreign_key_stops_the_grouped_ask_but_not_the_ungrouped_total(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    _lake_exec(
        rigged.rig.lake,
        "INSERT INTO bronze.public_schools (school_id, district_id, name, budget) "
        "VALUES (5, 99, 'Elm', 9.25)",
    )
    grouped = rigged.plan(
        "What is the total budget of all schools by district?",
        BUDGET,
        group_by=((DISTRICTS, "name"),),
    )
    assert grouped["abstained"] is True and grouped["badge"] == "ABSTAIN"
    assert grouped["rows"] == [] and grouped["values"] == []
    assert "gap: ontology_unverified" in str(grouped["text"]), grouped["text"]
    assert rigged.executed == []

    total = rigged.plan(TOTAL_Q, BUDGET)
    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    assert oracle == [{"v": Decimal("370.00")}]  # the orphan school is a school
    _assert_answer(total, oracle, "370")
    assert len(rigged.executed) == 1


# ---------------------------------------------------------------- tampered rows (E12, memory store)


def _tamper(rigged: _Rigged, name: str, **edit: Any) -> None:
    store = rigged.env.store
    idx = next(i for i, m in enumerate(store._measures) if str(m.id) == rigged.row_id(name))
    store._measures[idx] = replace(store._measures[idx], **edit)


@pytest.mark.parametrize(
    "edit",
    [{"column_name": "name"}, {"description": "Edited by hand."}, {"aggregate": "avg"}],
)
def test_a_confirmed_row_edited_without_its_hash_never_answers(
    rigged: _Rigged, edit: dict[str, Any]
) -> None:
    rigged.confirm(BUDGET)
    _tamper(rigged, BUDGET, **edit)
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert rigged.executed == [] and rigged.wire_measures == {}


def test_a_hostile_column_with_a_recomputed_hash_executes_nothing(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    hostile = 'x"); DROP TABLE bronze.public_schools; --'
    row = rigged.env.row(rigged.row_id(BUDGET))
    _tamper(
        rigged,
        BUDGET,
        column_name=hostile,
        definition_hash=measure_definition_hash(
            row.name, row.grain, row.aggregate, hostile, row.description
        ),
    )
    before = _types(rigged.rig.lake, "bronze.public_schools")
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert rigged.executed == [], "nothing the hostile row produced reached the lake"
    assert rigged.wire_measures == {}
    assert _types(rigged.rig.lake, "bronze.public_schools") == before
    assert _oracle(rigged.rig.lake, "SELECT COUNT(*) AS n FROM bronze.public_schools") == [{"n": 4}]


def test_a_confirmed_row_with_no_ledger_pointer_never_answers(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    _tamper(rigged, BUDGET, ledger_entry_id=None)
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert rigged.executed == [] and rigged.wire_measures == {}


# ---------------------------------------------------------------- a store that cannot be read


@pytest.mark.parametrize("confirmed", [False, True])
def test_a_failing_measures_read_is_a_named_abstain_never_a_silent_drop(
    rigged: _Rigged, monkeypatch: pytest.MonkeyPatch, confirmed: bool
) -> None:
    if confirmed:
        rigged.confirm(BUDGET)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("measures table unreachable")

    monkeypatch.setattr(rigged.env.store, "measures_for_space", boom)
    asked_before = len(rigged.rig.cortex.insights)
    env = rigged.plan(TOTAL_Q, BUDGET, query_sql=SUM_SQL)
    _assert_abstain(env, "ontology_store_unavailable")
    # nothing was answered from a Space whose measures we could not read, SQL included
    assert rigged.executed == []
    assert len(rigged.rig.cortex.insights) == asked_before, "Cortex was asked anyway"
    assert rigged.rig.cortex.asks == []


# ================================================================ step 7: the named abstains
#
# ``no_declared_link`` (the source never linked these tables) and ``no_declared_measure`` (no
# steward-confirmed measure answers) each say one plain sentence, name nothing the model chose,
# and offer the Space's usable confirmed measures. ``unverified_join`` keeps every OTHER join
# refusal; ``no_path`` stays on the demo ontology.

GRANTS = "bronze.public_grants"
GRANT_TABLE = ("public", "grants")
GRANT_DATA = {
    '"public"."grants"': (
        ["grant_id", "title", "amount"],
        [[1, "Lunch", Decimal("5.00")], [2, "Books", Decimal("7.50")]],
    )
}
GRANT_TYPES = {
    "public.grants": [
        ("grant_id", "integer", 32, 0),
        ("title", "text", None, None),
        ("amount", "numeric", 10, 2),
    ]
}
SCHOOLS_GRANTS_SQL = (
    "SELECT SUM(s.budget) AS total_budget FROM bronze.public_schools s "
    "JOIN bronze.public_grants g ON s.school_id = g.grant_id"
)
PLAIN_LINK = "The data does not declare a link between these tables, so I will not join them."
PLAIN_MEASURE = "No steward has confirmed a measure that answers this question."


@pytest.fixture()
def linkless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> _Rigged:
    """The schools Space plus a ``grants`` table that has a key and NO foreign key at all."""
    monkeypatch.setattr(ingest277, "PKS", [*ingest277.PKS, ("public", "grants", "grant_id", 1)])
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Linkless")
    rig.ingest(
        space,
        tables=[*ingest277.TABLES, GRANT_TABLE],
        data={**ingest277.DATA, **GRANT_DATA},
        types={**ingest277.TYPES, **GRANT_TYPES},
    )
    env = _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())
    return _Rigged(env)


def _suggestions(rigged: _Rigged, *names: str) -> list[str]:
    return [f"{n}: {rigged.env.row(rigged.row_id(n)).description}" for n in sorted(names)]


# ---------------------------------------------------------------- pure: heads, sentences, relabel


OLD_HEADS = {
    "unknown_measure", "no_path", "unknown_object", "unknown_column", "ontology_unverified",
    "missing_metric", "missing_ontology", "missing_join", "fanout_refused", "ambiguous_path",
    "unknown_link", "coverage_invalid", "insights_unarmed", "insights_refused",
    "insights_unauthorized", "insights_timeout", "insights_call_cap",
    "insights_no_sql_no_ranking", "insights_bearer_missing",
    "insights_bearer_insecure_transport", "unhonored_qualifier", "unverified_join",
    "no_declared_measure", "ontology_store_unavailable", "untyped_numeric", "ambiguous_measure",
    "rank_window_unhandled_terms", "ungrounded_qualifier", "rank_window_entity_mismatch",
    "rank_window_open_ended", "unrequested_offset", "loop_exhausted", "empty_result_unverified",
    "no_sql", "multi_statement", "warehouse_missing",
}  # fmt: skip


def test_every_existing_head_stays_and_the_three_new_ones_are_named() -> None:
    assert OLD_HEADS <= GAP_REASONS
    for head in ("no_declared_link", "unmatched_filter_value", "empty_aggregate"):
        assert head in GAP_REASONS and gap_reason_name(f"{head}: anything") == head


@pytest.mark.parametrize(
    ("reason", "sentence"),
    [
        ("no_declared_measure: m1 is not a measure this Space declares", PLAIN_MEASURE),
        (
            "no_declared_link: unverified_join:bronze.a x bronze.b has no declared link",
            PLAIN_LINK,
        ),
        (
            "unmatched_filter_value: bronze.public_districts.name = 'north'",
            "That value does not appear in the data exactly as written.",
        ),
        ("empty_aggregate: schools_budget_sum", "The measure has no values for this question."),
    ],
)
def test_each_new_head_says_one_plain_sentence_then_the_gap_and_names_nothing_else(
    reason: str, sentence: str
) -> None:
    text = customer_abstain_text(reason)
    head = gap_reason_name(reason)
    assert text.startswith(sentence + " I cannot certify"), text
    assert f"(gap: {head})" in text, text
    # The tail is the model's or the asker's: relation names, measure ids and values stay in
    # the assumptions, never the sentence.
    for leaked in ("bronze", "m1", "north", "schools_budget_sum", "unverified_join"):
        assert leaked not in text, (leaked, text)


def test_heads_with_no_plain_sentence_render_exactly_as_before() -> None:
    assert customer_abstain_text("no_path: no chain from sale to orphan") == (
        "I cannot certify an ontology-grounded query for that question "
        "(gap: no_path: no chain from sale to orphan), so I am not executing one."
    )
    assert "gap:" not in customer_abstain_text("submit_failed")


@pytest.mark.parametrize(
    ("why", "relabelled"),
    [
        (
            "unverified_join:bronze.public_districts x bronze.public_enrollments "
            "has no declared link",
            True,
        ),
        ("unverified_join:bronze.a x bronze.b joined on columns no declared link names", False),
        ("unverified_join:link enrollments_school_id_fkey is not verified", False),
        ("unverified_join:fan_trap", False),
        ("unverified_join:cross_product", False),
        ("unverified_join:non_link_predicate", False),
        ("unverified_join:check_unavailable", False),
        ("unverified_join:parse", False),
        # only a join refusal is ever relabelled, whatever else ends the same way
        ("no_path: bronze.a x bronze.b has no declared link", False),
        ("validate:explain: has no declared link", False),
    ],
)
def test_relabel_join_gap_only_names_a_missing_declaration(why: str, relabelled: bool) -> None:
    got = relabel_join_gap(why)
    assert got == (f"no_declared_link: {why}" if relabelled else why)
    assert relabel_join_gap(None) is None and relabel_join_gap("") == ""


# ---------------------------------------------------------------- the SQL branch (E10)


def test_sql_over_tables_the_source_never_linked_is_no_declared_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    """E10, SQL branch. Enrollments to districts: the source declares no link at all."""
    from test_onto_derive_01 import BROKEN_Q, BROKEN_SQL, VERIFIED_Q, WRONG_JOIN_SQL

    rig = _space(tmp_path, monkeypatch, minter)
    sql = (
        "SELECT COUNT(*) AS n FROM bronze.public_enrollments e "
        "JOIN bronze.public_districts d ON e.school_id = d.district_id"
    )
    # It would execute and answer (2, a number about a join nobody declared): the refusal is
    # the rule, not a failing statement.
    assert _oracle(rig.lake, sql) == [{"n": 2}]
    env = rig.ask("How many enrollments are there per district?", {"query_sql": sql})
    text = _assert_abstain(env, "no_declared_link")
    assert PLAIN_LINK in text
    assert "bronze.public_enrollments" not in text and "unverified_join" not in text, text
    # The original refusal is whole in the assumptions, behind the new head.
    assert (
        "no_declared_link: unverified_join:bronze.public_districts x bronze.public_enrollments "
        "has no declared link" in _reasons(env)
    ), env.get("assumptions")
    assert rig.cortex.executed == []
    assert env["suggestions"] == [], "nothing is confirmed on this Space, so nothing is offered"

    # The other join refusals keep their head: a join on the wrong columns, and a link that
    # failed verification.
    wrong = rig.ask(VERIFIED_Q, {"query_sql": WRONG_JOIN_SQL, "plan_source": "ontology_plan"})
    _assert_abstain(wrong, "unverified_join")
    assert "no_declared_link" not in str(wrong["text"]) + _reasons(wrong)
    broken = rig.ask(BROKEN_Q, {"query_sql": BROKEN_SQL, "plan_source": "ontology_plan"})
    _assert_abstain(broken, "ontology_unverified")
    assert rig.cortex.executed == []


def test_the_contract_ask_sql_over_unlinked_tables_is_no_declared_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    """E10, check_sql_against_space: the contract ask's own SQL gets the same name."""
    from test_onto_derive_01 import VERIFIED_Q, WRONG_JOIN_SQL

    rig = _space(tmp_path, monkeypatch, minter)
    sql = (
        "SELECT COUNT(*) AS n FROM bronze.public_enrollments e "
        "JOIN bronze.public_districts d ON e.school_id = d.district_id"
    )

    def _ask(sql_used: str) -> Any:
        def ask(req: Any) -> AskResponse:
            rig.cortex.asks.append(req)
            return AskResponse(
                answer="There are 2.",
                abstained=False,
                badge="generated",
                route="generated",
                sql_used=sql_used,
                rows=[{"n": 2}],
            )

        return ask

    monkeypatch.setattr(rig.cortex, "ask", _ask(sql))
    env = rig.ask(VERIFIED_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    text = _assert_abstain(env, "no_declared_link")
    assert PLAIN_LINK in text and "bronze.public_enrollments" not in text
    assert "has no declared link" in _reasons(env)
    assert rig.cortex.executed == []

    monkeypatch.setattr(rig.cortex, "ask", _ask(WRONG_JOIN_SQL))
    _assert_abstain(rig.ask(VERIFIED_Q, {}), "unverified_join")


def test_sql_over_unlinked_tables_offers_what_a_steward_confirmed(linkless: _Rigged) -> None:
    linkless.confirm(BUDGET, COUNT)
    linkless.env.propose()  # proposing again confirms nothing and offers nothing
    env = linkless.sql(TOTAL_Q, SCHOOLS_GRANTS_SQL)
    text = _assert_abstain(env, "no_declared_link")
    assert PLAIN_LINK in text and GRANTS not in text
    assert env["suggestions"] == _suggestions(linkless, BUDGET, COUNT)
    assert env["values"] == [] and env["rows"] == [] and not env.get("drillthrough_token")
    assert linkless.executed == []
    assert linkless.rig.cortex.asks == []


# ---------------------------------------------------------------- the typed branch (E10, E06 shape)


def test_a_typed_plan_grouped_by_an_unlinked_object_is_no_declared_link(
    linkless: _Rigged,
) -> None:
    linkless.confirm(BUDGET)
    env = linkless.plan(
        "What is the total budget of all schools by grant title?",
        BUDGET,
        group_by=((GRANTS, "title"),),
        query_sql=SUM_SQL,
    )
    text = _assert_abstain(env, "no_declared_link")
    assert PLAIN_LINK in text and GRANTS not in text and BUDGET not in text, text
    assert f"no_declared_link: no chain of verified many-to-one links from '{SCHOOLS}'" in (
        _reasons(env)
    ), env.get("assumptions")
    assert env["suggestions"] == _suggestions(linkless, BUDGET)
    # No fallback: Cortex's SQL (valid, and it would answer) never ran.
    assert linkless.executed == [] and linkless.rig.cortex.asks == []


def test_a_typed_filter_on_an_unlinked_object_is_no_declared_link(linkless: _Rigged) -> None:
    linkless.confirm(BUDGET)
    env = linkless.plan(
        "What is the total budget of schools with a Lunch grant?",
        BUDGET,
        filters=((GRANTS, "title", "=", "Lunch"),),
    )
    text = _assert_abstain(env, "no_declared_link")
    assert PLAIN_LINK in text and "Lunch" not in text
    assert "so it cannot be filtered on" in _reasons(env)
    assert linkless.executed == []


def test_a_declared_link_the_plan_cannot_use_keeps_its_own_reason(linkless: _Rigged) -> None:
    """districts <- schools is declared (many-to-one the other way). Grouping a DISTRICT count
    by school is not "no declared link": the sentence would be false, so ``no_path`` stays."""
    linkless.confirm("districts_count")
    env = linkless.plan(
        "How many districts are there by school?",
        "districts_count",
        group_by=((SCHOOLS, "name"),),
    )
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    text = str(env["text"])
    assert "gap: no_path" in text, text
    assert "no_declared_link" not in text + _reasons(env)
    assert linkless.executed == []


def test_a_declared_verified_link_still_answers_with_oracle_rows(linkless: _Rigged) -> None:
    """The relabel is for a missing declaration only: next to an unlinked table, the declared
    and verified schools-to-districts link still answers, rows against the oracle."""
    linkless.confirm(BUDGET)
    env = linkless.plan(
        "What is the total budget of schools in the North district?",
        BUDGET,
        filters=((DISTRICTS, "name", "=", "North"),),
    )
    oracle = _oracle(
        linkless.rig.lake,
        "SELECT SUM(s.budget) AS v FROM bronze.public_schools s "
        "JOIN bronze.public_districts d ON s.district_id = d.district_id WHERE d.name = 'North'",
    )
    assert oracle == [{"v": Decimal("310.75")}]
    _assert_answer(env, oracle, "310.75")
    assert env["suggestions"] == []


# ---------------------------------------------------------------- no_declared_measure (E02, E11)


def test_an_undeclared_measure_says_so_plainly_and_offers_the_confirmed_ones(
    linkless: _Rigged,
) -> None:
    linkless.confirm(BUDGET, COUNT)
    env = linkless.plan(TOTAL_Q, "total_students")
    text = _assert_abstain(env, "no_declared_measure")
    assert PLAIN_MEASURE in text and "total_students" not in text, text
    assert "no_declared_measure: total_students is not a measure this Space declares" in _reasons(
        env
    ), env.get("assumptions")
    assert env["suggestions"] == _suggestions(linkless, BUDGET, COUNT)
    assert linkless.executed == []


def test_a_proposed_only_space_has_nothing_to_offer(rigged: _Rigged) -> None:
    env = rigged.plan(TOTAL_Q, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert PLAIN_MEASURE in text and BUDGET not in text
    assert env["suggestions"] == [], "a proposal is not an answer anyone may be pointed at"
    assert "is not a measure this Space declares" in _reasons(env)


@pytest.mark.parametrize(
    ("ddl", "why"),
    [
        ("ALTER TABLE bronze.public_schools DROP COLUMN budget", "column_missing"),
        ("ALTER TABLE bronze.public_schools ALTER budget TYPE VARCHAR", "column_type_changed"),
        (
            "INSERT INTO bronze.public_schools SELECT * FROM bronze.public_schools "
            "WHERE school_id = 1",
            "grain_unverified",
        ),
    ],
)
def test_a_measure_that_cannot_be_used_now_says_why_behind_the_head(
    rigged: _Rigged, ddl: str, why: str
) -> None:
    rigged.confirm(BUDGET, "districts_count")
    _lake_exec(rigged.rig.lake, ddl)
    env = rigged.plan(TOTAL_Q, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert PLAIN_MEASURE in text
    assert why not in text and BUDGET not in text, "the reason is for the assumptions"
    assert f"no_declared_measure: {BUDGET} is not usable now: {why}" in _reasons(env), env[
        "assumptions"
    ]
    assert rigged.executed == []
    # What can still answer is offered; what just failed is not.
    assert env["suggestions"] == _suggestions(rigged, "districts_count")


def test_a_tampered_row_is_unusable_with_its_reason_and_never_offered(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    _tamper(rigged, BUDGET, description="Edited by hand.")
    env = rigged.plan(TOTAL_Q, BUDGET)
    _assert_abstain(env, "no_declared_measure")
    assert f"{BUDGET} is not usable now: integrity" in _reasons(env)
    assert env["suggestions"] == [] and rigged.executed == []


def test_the_contract_ask_over_unlinked_tables_offers_what_a_steward_confirmed(
    linkless: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    linkless.confirm(BUDGET)

    def ask(req: Any) -> AskResponse:
        linkless.rig.cortex.asks.append(req)
        return AskResponse(
            answer="Total budget is 360.75.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=SCHOOLS_GRANTS_SQL,
            rows=[{"total_budget": Decimal("360.75")}],
        )

    monkeypatch.setattr(linkless.rig.cortex, "ask", ask)
    env = linkless.space.ask(TOTAL_Q, {})
    assert linkless.rig.cortex.asks, "the contract ask was not reached"
    text = _assert_abstain(env, "no_declared_link")
    assert PLAIN_LINK in text and "360.75" not in text and GRANTS not in text
    assert env["suggestions"] == _suggestions(linkless, BUDGET)
    assert linkless.executed == []


# ================================================================ step 10: the confirmed lane
#
# A confirmed measure answers, says so, and is the ONLY source of its number: it beats valid
# generated SQL when DMS's own reading of the question agrees (router precedence), it is vetoed
# when it does not (fit), its filter values must exist as landed (rule 12), and a plan that
# fails on this lane abstains with its name and never falls back to the model's SQL.
# The badge is CONFIRMED_MEASURE_BADGE (still L2_VALIDATED); generated SQL that no measure
# stands behind keeps today's path and carries no ``measure_basis``.

AVG_SQL = "SELECT AVG(s.budget) AS avg_budget FROM bronze.public_schools s"
AVG_Q = "What is the average budget of schools?"
RENAMED = "school_budget_total"


def _line(env: dict[str, Any]) -> str:
    lines = [str(a) for a in env.get("assumptions") or [] if str(a).startswith(CONFIRMED_PREFIX)]
    assert len(lines) == 1, env.get("assumptions")
    return lines[0]


def _assert_basis(env: dict[str, Any], rigged: _Rigged, name: str) -> None:
    """measure_basis and the sentence both describe the stored confirmed row, field by field."""
    row = rigged.env.row(rigged.row_id(name))
    basis = env["measure_basis"]
    assert tuple(basis) == CONFIRMED_BASIS_KEYS, tuple(basis)
    assert basis == {
        "status": "confirmed",
        "measure_id": str(row.id),
        "measure": row.name,
        "definition_hash": row.definition_hash,
        "version_id": str(row.version_id),
        "confirmed_at": row.decided_at.isoformat() if row.decided_at else None,
        "ledger_entry_id": row.ledger_entry_id,
        "grain": SCHOOLS,
        "columns_checked": True,
        "persisted": False,  # the in-memory store: said out loud below
    }, basis
    line = _line(env)
    assert row.name in line and row.definition_hash[:12] in line, line
    assert str(row.ledger_entry_id) in line and "steward account" in line, line
    assert line.endswith(CONFIRMED_MEMORY_ONLY_SUFFIX), line
    assert (row.decided_at.date().isoformat() if row.decided_at else "?") in line, line


def _assert_no_basis(env: dict[str, Any]) -> None:
    assert "measure_basis" not in env, env.get("measure_basis")
    assert not any(str(a).startswith(CONFIRMED_PREFIX) for a in env.get("assumptions") or [])


def test_e01_a_confirmed_answer_carries_its_basis_and_one_matching_line(
    rigged: _Rigged,
) -> None:
    """E01. Confirm with a rename (the steward's lever), ask with a plan naming the new name AND a
    wrong ``query_sql``. Rows == oracle, the figure is in the text, the basis is the confirmed
    row's, and the only statement the lake saw is the compile of the confirmed definition."""
    done = rigged.env.confirm(rigged.views[BUDGET], name=RENAMED)
    env = rigged.plan(TOTAL_Q, RENAMED, query_sql=WRONG_SQL)

    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    assert oracle == [{"v": Decimal("360.75")}]
    _assert_answer(env, oracle, "360.75")
    _assert_basis(env, rigged, BUDGET)
    assert env["measure_basis"]["measure"] == RENAMED
    confirmed_hash = done["measure"]["definition_hash"] if "measure" in done else None
    assert env["measure_basis"]["definition_hash"] == confirmed_hash, (done, env["measure_basis"])
    assert rigged.executed == [rigged.probe_sql(BUDGET)] and WRONG_SQL not in rigged.executed
    assert rigged.wire_measures.keys() == {RENAMED}
    assert rigged.rig.cortex.asks == []


def test_the_basis_survives_the_pii_masker_whole(rigged: _Rigged) -> None:
    """The envelope leaves live_ask through the PII scan. A digest or id with a long digit run
    would be read as a phone or account number; ``measure_basis`` must reach the customer
    byte for byte (compared with the stored row, not with the scan's own output)."""
    rigged.confirm(BUDGET)
    env = rigged.plan(TOTAL_Q, BUDGET)
    row = rigged.env.row(rigged.row_id(BUDGET))
    assert env["measure_basis"]["definition_hash"] == row.definition_hash
    assert "DMSMASK" not in json.dumps(env["measure_basis"])
    assert "DMSMASK" not in _line(env)


def test_e05_a_confirmed_measure_beats_valid_generated_sql_when_the_fit_agrees(
    rigged: _Rigged,
) -> None:
    """E05. Cortex returned valid SQL (a wrong constant) and ranked the confirmed measure first;
    the question and the measure agree, so the measure's compiled number answers and the model's
    SQL never reaches submit."""
    rigged.confirm(BUDGET)
    env = rigged.ranked(TOTAL_Q, BUDGET, WRONG_SQL)
    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    _assert_answer(env, oracle, "360.75")
    _assert_basis(env, rigged, BUDGET)
    assert rigged.executed == [rigged.probe_sql(BUDGET)], rigged.executed
    assert WRONG_SQL not in rigged.executed
    assert "prefer:confirmed_measure" in _reasons(env), env["assumptions"]
    assert rigged.rig.cortex.asks == []


def test_e05_the_same_ask_without_a_ranking_stays_on_the_generated_sql_path(
    rigged: _Rigged,
) -> None:
    """No ranked measure, no precedence: Cortex's SQL answers exactly as it did before, with
    no basis and no confirmed line (it is not the measure's number, and does not say it is)."""
    rigged.confirm(BUDGET)
    env = rigged.sql(TOTAL_Q, SUM_SQL)
    _assert_answer(env, _oracle(rigged.rig.lake, SUM_SQL), "360.75")
    _assert_no_basis(env)
    assert rigged.executed == [SUM_SQL]


# ---------------------------------------------------------------- the fit veto (E04)


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        (AVG_Q, "aggregate_cue"),
        ("How many schools have a budget?", "aggregate_cue"),
        ("What is the highest budget of schools?", "aggregate_cue"),
        ("What is the rate of budget of schools?", "quantity_cue"),
        ("What is the total revenue of all districts?", "no_match"),
    ],
)
def test_e04_a_plan_the_fit_rejects_abstains_and_never_runs_the_measure(
    rigged: _Rigged, question: str, reason: str
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(question, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert "360.75" not in text and BUDGET not in text
    assert f"{BUDGET} fit_disagrees:{reason}" in _reasons(env), env["assumptions"]
    assert "measure_basis" not in env and rigged.executed == []
    assert rigged.rig.cortex.asks == []
    assert env["suggestions"] == _suggestions(rigged, BUDGET)  # what CAN be asked


def test_e04_the_averaging_question_is_answered_by_the_sql_never_by_the_sum_measure(
    rigged: _Rigged,
) -> None:
    """Cortex ranked the SUM measure for an average question and also sent valid AVG SQL.
    The fit refuses the measure, so the SQL answers on today's generated path: the figure is
    the average (90.1875), never the confirmed SUM (360.75), and nothing is certified or
    labelled as the confirmed measure. (Step 9 will label this lane L2_UNCONFIRMED.)"""
    rigged.confirm(BUDGET)
    env = rigged.ranked(AVG_Q, BUDGET, AVG_SQL)
    oracle = _oracle(rigged.rig.lake, AVG_SQL)
    assert oracle == [{"avg_budget": Decimal("90.1875")}]
    assert env["abstained"] is False and env["badge"] == "L2_VALIDATED"
    assert env["badge"] not in ("L0_CERTIFIED", "L1_GOVERNED_METRIC")
    assert _multiset(env["rows"]) == _multiset(oracle)
    text = str(env["text"])
    assert "90.1875" in text and "360.75" not in text, text
    _assert_no_basis(env)
    assert rigged.executed == [AVG_SQL]
    assert "confirmed_measure_not_preferred:aggregate_cue" in _reasons(env), env["assumptions"]


def test_e05_slots_that_cannot_be_bound_leave_the_sql_on_the_generated_path(
    rigged: _Rigged,
) -> None:
    """The fit agrees (total / budget / schools) but a breakdown by an undeclared dimension has
    no typed plan to prefer: the grouped SQL answers as before (generated, no basis). Never the
    measure's ungrouped total standing in for the breakdown."""
    rigged.confirm(BUDGET)
    sql = (
        "SELECT s.district_id, SUM(s.budget) AS total_budget FROM bronze.public_schools s "
        "GROUP BY s.district_id"
    )
    env = rigged.ranked("What is the total budget of schools per region?", BUDGET, sql)
    oracle = _oracle(rigged.rig.lake, sql)
    assert env["abstained"] is False, (env["text"], env["assumptions"])
    assert _multiset(env["rows"]) == _multiset(oracle)
    assert "360.75" not in str(env["text"]), env["text"]
    _assert_no_basis(env)
    reasons = _reasons(env)
    assert "prefer:confirmed_measure" not in reasons
    assert "confirmed_measure_not_preferred" not in reasons, "fit agreed; the slots did not bind"
    assert rigged.executed == [sql]


# ---------------------------------------------------------------- rule 12 at ask time (E13)

NORTH_Q = "What is the total budget of schools in the north district?"


def test_e13_a_filter_value_that_is_not_in_the_column_as_landed_abstains_not_zero(
    rigged: _Rigged,
) -> None:
    """'north' is not 'North'. The plan would compile, execute and total nothing under a
    confirmed badge. It abstains by name; the model's valid SQL is not used instead."""
    rigged.confirm(BUDGET)
    env = rigged.plan(
        NORTH_Q, BUDGET, filters=((DISTRICTS, "name", "=", "north"),), query_sql=SUM_SQL
    )
    text = _assert_abstain(env, "unmatched_filter_value")
    assert "That value does not appear in the data exactly as written." in text, text
    assert "north" not in text.replace("(gap: unmatched_filter_value)", ""), text
    assert "'north'" in _reasons(env), env["assumptions"]
    assert "measure_basis" not in env
    assert rigged.executed == [] and rigged.rig.cortex.asks == []

    ok = rigged.plan(NORTH_Q, BUDGET, filters=((DISTRICTS, "name", "=", "North"),))
    oracle = _oracle(
        rigged.rig.lake,
        "SELECT SUM(s.budget) AS v FROM bronze.public_schools s "
        "JOIN bronze.public_districts d ON s.district_id = d.district_id WHERE d.name = 'North'",
    )
    assert oracle == [{"v": Decimal("310.75")}]
    _assert_answer(ok, oracle, "310.75")
    _assert_basis(ok, rigged, BUDGET)


def test_e13_one_missing_value_in_an_in_list_abstains(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(
        "What is the total budget of schools in the North and West districts?",
        BUDGET,
        filters=((DISTRICTS, "name", "IN", ["North", "West"]),),
    )
    _assert_abstain(env, "unmatched_filter_value")
    assert "'West'" in _reasons(env) and rigged.executed == []


def test_e13_a_sum_that_is_null_is_empty_aggregate_not_a_figure(rigged: _Rigged) -> None:
    """Every budget goes NULL after the measure was confirmed. The column and its type are
    unchanged, so the measure still loads; SUM is NULL. That is 'no value', not an answer."""
    rigged.confirm(BUDGET)
    _lake_exec(rigged.rig.lake, "UPDATE bronze.public_schools SET budget = NULL")
    assert _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools") == [
        {"v": None}
    ]
    env = rigged.plan(TOTAL_Q, BUDGET, query_sql=SUM_SQL)
    text = _assert_abstain(env, "empty_aggregate")
    assert "The measure has no values for this question." in text, text
    assert "measure_basis" not in env and "None" not in text
    assert len(rigged.executed) == 1 and rigged.executed != [SUM_SQL]  # compiled ran; SQL did not
    assert rigged.rig.cortex.asks == []


def test_e13_a_grouped_plan_with_no_rows_is_empty_aggregate(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    _lake_exec(rigged.rig.lake, "DELETE FROM bronze.public_schools")
    env = rigged.plan(
        "What is the total budget of all schools by district?",
        BUDGET,
        group_by=((DISTRICTS, "name"),),
    )
    _assert_abstain(env, "empty_aggregate")
    assert "measure_basis" not in env


def test_e13_a_filter_that_matches_the_dimension_but_no_fact_rows_is_empty_aggregate(
    rigged: _Rigged,
) -> None:
    """'North' exists as a district, so the value probe passes; its schools are gone, so the
    total is NULL. Still no figure."""
    rigged.confirm(BUDGET)
    _lake_exec(rigged.rig.lake, "DELETE FROM bronze.public_schools WHERE district_id = 1")
    env = rigged.plan(NORTH_Q, BUDGET, filters=((DISTRICTS, "name", "=", "North"),))
    _assert_abstain(env, "empty_aggregate")


def test_e13_a_count_of_an_empty_table_is_a_real_zero(rigged: _Rigged) -> None:
    """COUNT(*) over no rows is 0, a number, not a missing value: it still answers."""
    rigged.confirm(COUNT)
    _lake_exec(rigged.rig.lake, "DELETE FROM bronze.public_schools")
    env = rigged.plan(COUNT_Q, COUNT)
    oracle = _oracle(rigged.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")
    assert oracle == [{"v": 0}]
    assert env["abstained"] is False, (env["text"], env["assumptions"])
    assert _multiset(env["rows"]) == _multiset(oracle)
    _assert_basis(env, rigged, COUNT)


# ------------------------------------------------------ the envelope rule (E14, no new badge)

_HASH = "9f2c4a7e" * 8
_BASIS: dict[str, Any] = {
    "status": "confirmed",
    "measure_id": "6f1a2b3c-4d5e-4f60-8a7b-0c1d2e3f4a5b",
    "measure": "schools_budget_sum",
    "definition_hash": _HASH,
    "version_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
    "confirmed_at": "2026-10-09T15:51:37.123456+00:00",
    "ledger_entry_id": "led_0123456789",
    "grain": "bronze.public_schools",
    "columns_checked": True,
    "persisted": True,
}
_LINE = (
    f"{CONFIRMED_PREFIX}schools_budget_sum (definition {_HASH[:12]}, confirmed 2026-10-09 "
    "by this workspace's steward account, ledger led_0123456789)"
)


def _built(
    basis: dict[str, Any] | None, *, badge: str = "L2_VALIDATED", line: str | None = _LINE
) -> dict[str, Any]:
    from dms_executor.envelope import build_answer_envelope

    return build_answer_envelope(
        answer_id="ans_unit",
        text="Found 1 row(s).\n  - total=360.75",
        badge=badge,
        rows=[{"total": Decimal("360.75")}],
        sql_used="SELECT SUM(s.budget) AS total FROM bronze.public_schools s",
        assumptions=["GEN-01 ontology compile", *([line] if line else [])],
        audit_id="led_unit",
        route="generated",
        measure_basis=basis,
    )


@pytest.mark.parametrize("badge", ["L2_VALIDATED", "L1_GOVERNED_METRIC"])
def test_e14_a_valid_confirmed_basis_is_kept_on_either_confirmed_rung(badge: str) -> None:
    from dms_executor.envelope import assert_envelope_valid

    env = _built(dict(_BASIS), badge=badge)
    assert env["abstained"] is False and env["badge"] == badge
    assert env["measure_basis"] == _BASIS
    assert_envelope_valid(env)


def test_e14_no_basis_means_no_key() -> None:
    env = _built(None, line=None)
    assert "measure_basis" not in env and env["badge"] == "L2_VALIDATED"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.pop("ledger_entry_id"),
        lambda b: b.update(extra="x"),
        lambda b: b.update(status="unconfirmed"),
        lambda b: b.update(definition_hash="abc"),
        lambda b: b.update(definition_hash=_HASH.upper()),
        lambda b: b.update(ledger_entry_id=""),
        lambda b: b.update(ledger_entry_id="led 1; drop"),
        lambda b: b.update(measure="Schools Budget"),
        lambda b: b.update(grain="bronze.public_schools; --"),
        lambda b: b.update(confirmed_at="yesterday"),
        lambda b: b.update(columns_checked=False),
        lambda b: b.update(persisted="yes"),
    ],
)
def test_e14_a_basis_the_shape_cannot_back_clamps_the_answer_to_abstain(mutate: Any) -> None:
    from dms_executor.envelope import assert_envelope_valid

    basis = dict(_BASIS)
    mutate(basis)
    env = _built(basis)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert "measure_basis" not in env and env["rows"] == [] and env["values"] == []
    assert any("measure basis invalid" in str(a) for a in env["assumptions"]), env["assumptions"]
    assert_envelope_valid(env)


@pytest.mark.parametrize(
    ("badge", "line"),
    [
        ("L0_CERTIFIED", _LINE),  # a basis never lifts a figure to the verified rung
        ("L2_ANOMALOUS", _LINE),
        ("L2_VALIDATED", None),  # a basis without the sentence a customer reads
    ],
)
def test_e14_the_badge_and_the_sentence_must_match_the_basis(badge: str, line: str | None) -> None:
    env = _built(dict(_BASIS), badge=badge, line=line)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["badge"]
    assert "measure_basis" not in env


def test_e14_assert_envelope_valid_rejects_a_basis_it_cannot_back() -> None:
    from dms_executor.envelope import assert_envelope_valid

    good = _built(dict(_BASIS))
    assert_envelope_valid(good)

    bad_hash = {**good, "measure_basis": {**_BASIS, "definition_hash": "0" * 63}}
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(bad_hash)
    # (the sentence itself is scanned like any assumption, so match it by its prefix)
    no_line = {
        **good,
        "assumptions": [a for a in good["assumptions"] if not a.startswith(CONFIRMED_PREFIX)],
    }
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(no_line)
    wrong_badge = {**good, "badge": "L0_CERTIFIED"}
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(wrong_badge)
    abstained = _built(None, line=None, badge="ABSTAIN")
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid({**abstained, "measure_basis": dict(_BASIS)})


def test_e14_the_basis_is_not_rewritten_by_the_pii_scan() -> None:
    """A digest with a 10-digit run, or an id like one, would be read as a phone or account
    number by the generic scan. The key is exempt from the scan because the builder pins every
    field to an identifier shape; the same string elsewhere is still scanned."""
    from dms_core.pii import mask_unknown_keys

    basis = {
        **_BASIS,
        "definition_hash": "0123456789" + "ab" * 27,
        "ledger_entry_id": "led_0123456789",
    }
    out = mask_unknown_keys({"measure_basis": basis, "note": "call 0123456789 now"})
    assert out["measure_basis"] == basis
    assert "0123456789" not in out["note"], "other keys are still scanned"
    env = _built(basis, line=_LINE.replace(_HASH[:12], "0123456789ab"))
    assert env["abstained"] is False and env["measure_basis"] == basis


def test_a_measure_the_lake_did_not_confirm_this_ask_cannot_answer(
    rigged: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prune is what marks a measure as checked against the lake as it is now. If it did
    not run, nothing else vouches for the column: the plan abstains and the SQL is not used."""
    from dms_executor import generative_ask as ga

    rigged.confirm(BUDGET)
    monkeypatch.setattr(ga, "prune_unusable_measures", lambda *a, **k: {})
    env = rigged.plan(TOTAL_Q, BUDGET, query_sql=SUM_SQL)
    _assert_abstain(env, "no_declared_measure")
    assert f"{BUDGET} is not usable now: not_checked_against_data" in _reasons(env)
    assert rigged.executed == [] and "measure_basis" not in env


# ---------------------------------------------------------------- E01 through the real route


def test_e01_confirmed_through_the_steward_route_with_a_rename_then_asked(
    rigged: _Rigged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The steward confirms over HTTP (gate, role, hash, rename, ledger), then a customer asks.
    The basis the envelope carries is the one the route returned, not one rebuilt from the row."""
    import dms_api.routes.spaces as space_routes
    from cortex_client.gate import ComplianceDecision
    from test_onto_confirm_01 import _Api, _ApiCortex

    env0 = rigged.env
    calls: list[dict[str, Any]] = []

    def gate(*, action: str, **kw: Any) -> Any:
        calls.append({"action": action, **kw})
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr(space_routes, "compliance_gate", gate)
    asking_cortex = env0.rig.app.state.cortex
    env0.rig.app.state.cortex = _ApiCortex(env0.ledger)
    api = _Api(env=env0, gate_calls=calls, verdict={"reason": None})
    api.settings()
    r = api.confirm(rigged.views[BUDGET], name=RENAMED)
    assert r.status_code == 200, r.text
    assert [c["action"] for c in calls] == ["spaces.ontology.measures.confirm"]
    body = r.json()
    confirmed = body.get("measure") or body
    assert confirmed["name"] == RENAMED and confirmed["state"] == "confirmed", body
    env0.rig.app.state.cortex = asking_cortex  # the ask talks to the engine, not the ledger shim

    env = rigged.plan(TOTAL_Q, RENAMED, query_sql=WRONG_SQL)
    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    _assert_answer(env, oracle, "360.75")
    basis = env["measure_basis"]
    assert basis["measure"] == RENAMED
    assert basis["definition_hash"] == confirmed["definition_hash"]
    assert basis["measure_id"] == confirmed["id"]
    assert basis["ledger_entry_id"] == confirmed["ledger_entry_id"]
    assert str(basis["ledger_entry_id"]) in _line(env)
    assert rigged.executed == [rigged.probe_sql(BUDGET)] and WRONG_SQL not in rigged.executed


# ---------------------------------------------------------------- grouped asks and the fit


GROUPED_Q = "What is the total budget of all schools by district?"
BY_DISTRICT = ((SCHOOLS, "district_id"),)
GROUPED_SQL = (
    "SELECT district_id, SUM(budget) AS v FROM bronze.public_schools GROUP BY district_id"
)


def test_a_grouped_plan_answers_the_by_district_question_it_computes(rigged: _Rigged) -> None:
    """'by district' is a breakdown the plan itself computes: the measure grouped by the
    district key IS the answer, so the fit does not veto it. Rows == the oracle grouping."""
    rigged.confirm(BUDGET)
    env = rigged.plan(GROUPED_Q, BUDGET, group_by=BY_DISTRICT)
    oracle = _oracle(rigged.rig.lake, GROUPED_SQL)
    assert _multiset(oracle) == _multiset(
        [{"d": 1, "v": Decimal("310.75")}, {"d": 2, "v": Decimal("50.00")}]
    )
    _assert_answer(env, oracle, "310.75")
    assert "50" in str(env["text"]) and "360.75" not in str(env["text"])
    _assert_basis(env, rigged, BUDGET)
    assert len(rigged.executed) == 1


def test_the_same_question_with_a_plan_that_does_not_group_is_not_answered_by_the_total(
    rigged: _Rigged,
) -> None:
    """The breakdown is only covered when the PLAN groups by it. An ungrouped plan for 'by
    district' would offer one total as the per-district answer: the fit refuses it."""
    rigged.confirm(BUDGET)
    env = rigged.plan(GROUPED_Q, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert "360.75" not in text and rigged.executed == []
    assert f"{BUDGET} fit_disagrees:qualifier_cue" in _reasons(env), env["assumptions"]


def test_a_plan_grouped_by_something_else_does_not_cover_the_word_asked(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(
        "What is the total budget of all schools by region?", BUDGET, group_by=BY_DISTRICT
    )
    _assert_abstain(env, "no_declared_measure")
    assert f"{BUDGET} fit_disagrees:qualifier_cue" in _reasons(env) and rigged.executed == []


def test_e13_a_grouped_answer_whose_every_group_is_null_is_empty_aggregate(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    _lake_exec(rigged.rig.lake, "UPDATE bronze.public_schools SET budget = NULL")
    env = rigged.plan(GROUPED_Q, BUDGET, group_by=BY_DISTRICT)
    text = _assert_abstain(env, "empty_aggregate")
    assert "None" not in text and "measure_basis" not in env and env["rows"] == []


# ------------------------------------------------------------ DMS_CLOOP_B cannot route around it


@pytest.fixture()
def cloop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The extract loop on, exactly as tests/test_c_loop_b.py opts in."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")


def test_cloop_a_ranked_confirmed_measure_that_fits_beats_the_loops_sql(
    rigged: _Rigged, cloop: None
) -> None:
    """With the loop on, valid ranked SQL used to go to the loop and answer there. The
    precedence is decided first: the measure's compiled number answers, the SQL never runs."""
    rigged.confirm(BUDGET)
    env = rigged.ranked(TOTAL_Q, BUDGET, WRONG_SQL)
    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    _assert_answer(env, oracle, "360.75")
    _assert_basis(env, rigged, BUDGET)
    assert rigged.executed == [rigged.probe_sql(BUDGET)] and WRONG_SQL not in rigged.executed
    assert "loop" not in env or not env["loop"], env.get("loop")


def test_cloop_a_ranking_with_no_sql_is_the_measures_to_answer_not_the_loops(
    rigged: _Rigged, cloop: None
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.space.ask(
        TOTAL_Q, {"plan_source": "ontology_plan", "ontology": {"metrics": [{"id": BUDGET}]}}
    )
    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    _assert_answer(env, oracle, "360.75")
    _assert_basis(env, rigged, BUDGET)
    assert rigged.executed == [rigged.probe_sql(BUDGET)]


def test_cloop_a_confirmed_plan_that_abstains_never_falls_back_to_the_loops_sql(
    rigged: _Rigged, cloop: None
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(
        NORTH_Q, BUDGET, filters=((DISTRICTS, "name", "=", "north"),), query_sql=SUM_SQL
    )
    _assert_abstain(env, "unmatched_filter_value")
    assert rigged.executed == [] and rigged.rig.cortex.asks == []


def test_cloop_when_the_fit_fails_the_loop_answers_the_sql_as_it_always_did(
    rigged: _Rigged, cloop: None
) -> None:
    """The measure is not the answer to an average question, so the loop's SQL is: the figure is
    the average, never the confirmed SUM, with no basis and no confirmed line."""
    rigged.confirm(BUDGET)
    env = rigged.ranked(AVG_Q, BUDGET, AVG_SQL)
    oracle = _oracle(rigged.rig.lake, AVG_SQL)
    assert env["abstained"] is False and env["badge"] == "L2_VALIDATED"
    assert _multiset(env["rows"]) == _multiset(oracle)
    assert "90.1875" in str(env["text"]) and "360.75" not in str(env["text"])
    _assert_no_basis(env)
    assert rigged.executed == [AVG_SQL]


# --------------------------------- a condition the question states and the plan drops (E15)

NORTH_SQL = (
    "SELECT SUM(s.budget) AS total_budget FROM bronze.public_schools s "
    "JOIN bronze.public_districts d ON s.district_id = d.district_id WHERE d.name = 'North'"
)
OVER_SQL = "SELECT SUM(budget) AS total_budget FROM bronze.public_schools WHERE budget > 100"
D2_SQL = "SELECT SUM(budget) AS total_budget FROM bronze.public_schools WHERE district_id = 2"
CONDITIONS = [
    ("What is the total budget of schools in the north district?", NORTH_SQL),
    ("What is the total budget of schools in the North district?", NORTH_SQL),
    ("What is the total budget of schools with a budget over 100?", OVER_SQL),
    ("What is the total budget of schools in district 2?", D2_SQL),
]


def _assert_the_sql_answered_not_the_measure(
    rigged: _Rigged, env: dict[str, Any], sql: str
) -> None:
    """The figure is the SQL's own (the oracle's, filtered), never the unfiltered 360.75, and the
    answer says nothing about a confirmed measure: no basis, no confirmed line."""
    oracle = _oracle(rigged.rig.lake, sql)
    figure = _norm(oracle[0]["total_budget"])
    assert figure != "360.75", "the case must be one where the filter changes the figure"
    assert env["abstained"] is False, (env["text"], env["assumptions"])
    assert _multiset(env["rows"]) == _multiset(oracle)
    assert figure in str(env["text"]) and "360.75" not in str(env["text"]), env["text"]
    _assert_no_basis(env)
    assert "confirmed_measure_not_preferred:unbound_constraint" in _reasons(env), env["assumptions"]
    assert "prefer:confirmed_measure" not in _reasons(env)
    assert rigged.executed == [sql], rigged.executed
    assert rigged.rig.cortex.asks == []


@pytest.mark.parametrize(("question", "sql"), CONDITIONS)
def test_e15_a_ranked_measure_never_answers_a_question_whose_condition_its_plan_drops(
    rigged: _Rigged, question: str, sql: str
) -> None:
    """Cortex returned correct filtered SQL AND ranked the measure first; the measure's plan has
    no filter, so its total (360.75) is not the answer to this question. The SQL answers."""
    rigged.confirm(BUDGET)
    env = rigged.ranked(question, BUDGET, sql)
    _assert_the_sql_answered_not_the_measure(rigged, env, sql)


@pytest.mark.parametrize(("question", "sql"), CONDITIONS)
def test_e15_the_same_holds_with_the_extract_loop_on(
    rigged: _Rigged, cloop: None, question: str, sql: str
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.ranked(question, BUDGET, sql)
    oracle = _oracle(rigged.rig.lake, sql)
    assert env["abstained"] is False and "360.75" not in str(env["text"]), env["text"]
    assert _multiset(env["rows"]) == _multiset(oracle)
    _assert_no_basis(env)
    assert rigged.executed == [sql]


@pytest.mark.parametrize(("question", "sql"), CONDITIONS)
def test_e15_a_ranking_with_no_sql_abstains_rather_than_state_the_unfiltered_total(
    rigged: _Rigged, question: str, sql: str
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.space.ask(
        question, {"plan_source": "ontology_plan", "ontology": {"metrics": [{"id": BUDGET}]}}
    )
    text = _assert_abstain(env, "no_declared_measure")
    assert "360.75" not in text and "measure_basis" not in env
    assert f"{BUDGET} fit_disagrees:unbound_constraint" in _reasons(env), env["assumptions"]
    assert rigged.executed == [] and rigged.rig.cortex.asks == []


def test_e15_a_typed_plan_without_the_filter_abstains_by_name(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(NORTH_Q, BUDGET)
    text = _assert_abstain(env, "no_declared_measure")
    assert "360.75" not in text
    assert "fit_disagrees:unbound_constraint ('north', 'district')" in _reasons(env)
    assert rigged.executed == [] and "measure_basis" not in env


def test_e15_a_plan_that_carries_the_condition_still_answers(rigged: _Rigged) -> None:
    """The gate is a coverage check, not a ban on filters: the plan that carries what the
    question states is answered by the measure, with its basis and the filtered oracle rows."""
    rigged.confirm(BUDGET)
    for question, flt, sql, figure in [
        (NORTH_Q, (DISTRICTS, "name", "=", "North"), NORTH_SQL, "310.75"),
        (
            "What is the total budget of schools in district 2?",
            (SCHOOLS, "district_id", "=", 2),
            D2_SQL,
            "50",
        ),
    ]:
        env = rigged.plan(question, BUDGET, filters=(flt,))
        oracle = _oracle(rigged.rig.lake, sql)
        _assert_answer(env, oracle, figure)
        _assert_basis(env, rigged, BUDGET)


def test_e15_a_plan_that_carries_part_of_the_condition_abstains(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(
        "What is the total budget of schools in the North and South districts?",
        BUDGET,
        filters=((DISTRICTS, "name", "=", "North"),),
    )
    _assert_abstain(env, "no_declared_measure")
    assert "fit_disagrees:unbound_constraint ('south')" in _reasons(env)
    assert rigged.executed == []


def test_e15_a_condition_nothing_can_check_is_a_gap_not_a_total(rigged: _Rigged) -> None:
    """'Last year' is a condition the measure has no column for: there is no honest figure. The
    qualifier gate names it before the fit is asked; either way the total is not stated."""
    rigged.confirm(BUDGET)
    env = rigged.plan("What is the total budget of schools last year?", BUDGET)
    text = _assert_abstain(env, "unhonored_qualifier:time_filter=last_1_year")
    assert "360.75" not in text and rigged.executed == []


# -------------------------------------------- a group with no value is not a figure (E15)


def test_e15_a_group_with_no_value_is_left_out_and_the_answer_says_so(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    _lake_exec(
        rigged.rig.lake, "UPDATE bronze.public_schools SET budget = NULL WHERE district_id = 2"
    )
    env = rigged.plan(GROUPED_Q, BUDGET, group_by=BY_DISTRICT)
    oracle = _oracle(
        rigged.rig.lake,
        "SELECT district_id, SUM(budget) AS v FROM bronze.public_schools "
        "GROUP BY district_id HAVING SUM(budget) IS NOT NULL",
    )
    assert _multiset(oracle) == _multiset([{"d": 1, "v": Decimal("310.75")}])
    _assert_answer(env, oracle, "310.75")
    _assert_basis(env, rigged, BUDGET)
    text = str(env["text"])
    assert "None" not in text, text
    assert f"Some groups have no value for {BUDGET} and are not shown." in text, text
    assert not any(v is None for row in env["rows"] for v in row.values()), env["rows"]
    assert f"excluded: groups with no value for {BUDGET}" in _reasons(env), env["assumptions"]


def test_e15_every_group_with_a_value_is_shown_and_nothing_is_said_about_the_rest(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(GROUPED_Q, BUDGET, group_by=BY_DISTRICT)
    assert env["abstained"] is False and len(env["rows"]) == 2
    assert "are not shown" not in str(env["text"])
    assert not any("excluded: groups" in a for a in env["assumptions"])


# ----------------------------------- the sentence a customer reads is not rewritten (E15)


def test_e15_the_confirmed_line_survives_the_pii_scan_whole() -> None:
    """A phone-shaped run in the ledger id (or in the digest prefix) is an identifier DMS minted,
    not a phone number: the sentence is exactly what the basis spells. Anything else in the
    assumptions that carries such a run is still masked."""
    from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

    basis = {
        **_BASIS,
        "definition_hash": "0123456789" + "ab" * 27,
        "ledger_entry_id": "led_0123456789",
    }
    line = confirmed_line(basis)
    assert "0123456789ab" in line and "led_0123456789" in line
    env = build_answer_envelope(
        answer_id="ans_unit",
        text="Found 1 row(s).\n  - total=360.75",
        badge="L2_VALIDATED",
        rows=[{"total": Decimal("360.75")}],
        sql_used="SELECT SUM(s.budget) AS total FROM bronze.public_schools s",
        assumptions=["GEN-01 ontology compile", line, "call 0123456789 now"],
        audit_id="led_unit",
        route="generated",
        measure_basis=basis,
    )
    assert env["abstained"] is False and env["measure_basis"] == basis
    assert env["assumptions"][1] == line, env["assumptions"]
    assert "0123456789" not in env["assumptions"][2], "a look-alike elsewhere is still scanned"
    assert_envelope_valid(env)


def test_e15_the_scan_exempts_only_the_sentence_a_valid_basis_spells() -> None:
    """The exemption is the narrowest thing that works: the exact sentence, only under
    ``assumptions``, only when every identifier it embeds has its shape."""
    from dms_core.measure_line import sanctioned_confirmed_line
    from dms_core.pii import mask_unknown_keys

    basis = {**_BASIS, "ledger_entry_id": "led_0123456789"}
    line = confirmed_line(basis)
    assert sanctioned_confirmed_line(basis) == line
    kept = mask_unknown_keys({"measure_basis": basis, "assumptions": [line]})
    assert kept["assumptions"] == [line]

    # the same words anywhere else, or one character off, are scanned like any text
    assert mask_unknown_keys({"measure_basis": basis, "note": [line]})["note"] != [line]
    off_by_one = line.replace("steward account", "steward  account")
    assert mask_unknown_keys({"measure_basis": basis, "assumptions": [off_by_one]})[
        "assumptions"
    ] != [off_by_one]
    assert mask_unknown_keys({"assumptions": [line]})["assumptions"] != [line], "no basis, no pass"

    # a basis with a field off its identifier shape sanctions nothing
    for field, bad in [
        ("ledger_entry_id", "led 0123456789; drop"),
        ("measure", "Call 0123456789"),
        ("definition_hash", "0123456789"),
        ("confirmed_at", "yesterday"),
    ]:
        broken = {**basis, field: bad}
        assert sanctioned_confirmed_line(broken) is None, field
        out = mask_unknown_keys({"measure_basis": broken, "assumptions": [confirmed_line(broken)]})
        assert out["assumptions"] != [confirmed_line(broken)], field
    assert sanctioned_confirmed_line(None) is None
    assert sanctioned_confirmed_line("measure confirmed: x") is None


def test_e15_the_sentence_is_spelled_once() -> None:
    """The executor's constants and the core spelling cannot drift apart."""
    from dms_core import measure_line

    assert measure_line.CONFIRMED_PREFIX == CONFIRMED_PREFIX
    assert measure_line.CONFIRMED_MEMORY_ONLY_SUFFIX == CONFIRMED_MEMORY_ONLY_SUFFIX
    from dms_executor import measure_basis

    assert measure_line.CONFIRMED_LINE_HASH_CHARS == measure_basis.CONFIRMED_LINE_HASH_CHARS


def test_e15_a_ledger_id_with_a_long_digit_run_reaches_the_customer_unmasked(
    rigged: _Rigged,
) -> None:
    ledger = rigged.env.ledger
    real = ledger.append

    def numbered(**kw: Any) -> Any:
        out = real(**kw)
        return type(out)(entry_id="led_0123456789", hash=out.hash)

    ledger.append = numbered  # type: ignore[method-assign]
    rigged.confirm(BUDGET)
    env = rigged.plan(TOTAL_Q, BUDGET)
    assert env["measure_basis"]["ledger_entry_id"] == "led_0123456789"
    line = _line(env)
    assert "led_0123456789" in line and "DMSMASK" not in line, line
    assert line == confirmed_line(env["measure_basis"])
