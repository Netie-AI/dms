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
from dms_executor.gen_path_refuse import GAP_REASONS, customer_abstain_text, gap_reason_name
from dms_executor.measure_basis import CONFIRMED_MEASURE_BADGE
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
