"""CONNECT-ASK-02 (dms#284) at the customer envelope: confirmed measures on the ask path.

Step 6 of the measure lane (load, prune, wire budget). Every case posts ``POST /v1/chat/ask``,
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
from dms_core.control_plane.onto_store import OntologyStore, measure_definition_hash
from dms_executor.measure_basis import CONFIRMED_MEASURE_BADGE
from dms_executor.space_ontology import set_ontology_store
from test_ingest_fixes_277 import _oracle, _Rig, _types
from test_onto_confirm_01 import SCHOOLS, _Env, _lake_exec, _Ledger
from test_onto_derive_01 import _assert_abstain, _Space, make_minter

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
