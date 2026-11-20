"""CONNECT-ASK-04 (dms#524, F-TBD-35): a Yes on held SQL re-enters the real door.

Before: with ``DMS_DEF_VALIDATE=1`` and ``DMS_RECONFIRM_RUN_HELD=1`` a
``def_validate`` reconfirm parked the generated SQL before the Space join rule
saw it. Yes ran that SQL in ``_run_held_sql`` behind two checks of its own, on a
local cursor: no join rule, no Cortex submit, no ledger entry, and an
``audit_id`` that was the constant ``ans_reconfirm_yes``. Measured on the parent
(``claude/integration-epic-178`` at ``583377f``): the wrong join served
``L2_VALIDATED``, "The confirmed query ran.", ``school_count`` 1.

Here a non-demo Space with a derived ontology (the ``_space`` fixture of
``tests/test_onto_derive_01.py``: three tables, one verified link, one broken
link) asks with a stub generate leg, takes the reconfirm token, and posts Yes to
``POST /v1/chat/ask/confirm``. Every case asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows). The Cortex fake
executes submits on the lake under the Cortex grant rule, so rows are real, and
it records every submit and ledger append it receives.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_onto_derive_01 as od
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from dms_core.ask import ExecutedTrace
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import set_ontology_store
from test_onto_derive_01 import (
    DISTRICTS,
    SCHOOLS,
    VERIFIED_Q,
    VERIFIED_SQL,
    WRONG_JOIN_SQL,
    _multiset,
    _oracle,
    _reasons,
    make_minter,
)

SESSION = "ses_onto_derive"
# Truth from the fixture constants, not from the lake and not from the SQL:
# schools whose district row is named North.
_NORTH = {d for d, name in od.DISTRICT_ROWS if name == "North"}
VERIFIED_TRUTH = sum(1 for _s, district, _n in od.SCHOOL_ROWS if district in _NORTH)
# The wrong join counts schools whose own id equals a North district id.
WRONG_FIGURE = sum(1 for school, _d, _n in od.SCHOOL_ROWS if school in _NORTH)
LEGACY_TEXT = "The confirmed query ran."
# The abstain wording of a refused Yes, kept from before this ticket.
YES_ABSTAIN_TEXT = (
    "I cannot run that query against the granted tables, so I am not answering it."
)


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case states its own flags. None leaks in from the shell."""
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_ONTO_FROM_DATA",
        "DMS_DEF_VALIDATE",
        "DMS_RECONFIRM_RUN_HELD",
        "DMS_AI_FIRST",
        "DMS_VQ_RAG",
        "DMS_SCHEMA_CONTEXT",
    ):
        monkeypatch.delenv(name, raising=False)
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()


def _gen(sql: str) -> dict[str, Any]:
    return {"query_sql": sql, "plan_source": "ontology_plan"}


class _Rig:
    """The onto-derive Space, with what Cortex received and what the ask ran."""

    def __init__(self, space: Any) -> None:
        self.space = space
        self.client = space.client
        self.cortex = space.cortex
        self.lake: Path = space.lake
        self.space_id: str = space.space_id
        self.exe = self.client.app.state.ask_service
        self.submits: list[Any] = []
        self.ledger: list[LedgerAppendRequest] = []
        self.traces: list[ExecutedTrace] = []
        self.entry_id = "led_connect_ask_04"
        inner_submit = self.cortex.submit

        def _submit(req: Any) -> Any:
            self.submits.append(req)
            return inner_submit(req)

        def _ledger(req: LedgerAppendRequest) -> LedgerAppendResponse:
            self.ledger.append(req)
            return LedgerAppendResponse(
                entry_id=self.entry_id, hash="hash_connect_ask_04_x", seq=len(self.ledger)
            )

        self.cortex.submit = _submit
        self.cortex.ledger_append = _ledger
        inner_take = self.exe.take_executed

        def _take() -> ExecutedTrace:
            got = inner_take()
            self.traces.append(got)
            return got

        self.exe.take_executed = _take

    def sql_submits(self) -> list[Any]:
        """Submits that carried a statement (a session bind carries none)."""
        return [r for r in self.submits if (getattr(r, "body", None) or {}).get("sql")]

    def ask(self, question: str, sql: str) -> dict[str, Any]:
        self.cortex.payload = _gen(sql)
        r = self.client.post(
            "/v1/chat/ask",
            json={"question": question, "session_id": SESSION, "space_id": self.space_id},
        )
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env

    def park(self, question: str, sql: str) -> str:
        """Ask under def-validate. Return the Yes token of the parked SQL."""
        from dms_executor.reconfirm import held_ready, peek_pending

        env = self.ask(question, sql)
        assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == [], env
        token = str(env["reconfirm"]["token"])
        pending = peek_pending(token)
        assert pending is not None and held_ready(pending), "no SQL was parked for a Yes"
        assert pending["sql"] == sql
        assert self.cortex.executed == [], "the first ask already executed something"
        return token

    def yes(self, token: str) -> dict[str, Any]:
        """Post Yes. ``traces`` then holds only what the confirm route read after it."""
        self.traces.clear()
        r = self.client.post("/v1/chat/ask/confirm", json={"token": token, "yes": True})
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env

    def trace(self) -> ExecutedTrace:
        """What the confirm route took for its audit row: the last non-empty read."""
        for got in reversed(self.traces):
            if got.statements or got.ledger:
                return got
        return ExecutedTrace()


def _held_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, **kw: Any
) -> _Rig:
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    return _Rig(od._space(tmp_path, monkeypatch, minter, **kw))


def _assert_yes_abstains(env: dict[str, Any], rig: _Rig, reason: str) -> str:
    """Rule 10a on a refused Yes: no row, no value, no token, nothing executed."""
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("rows"))
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert not env.get("drillthrough_token")
    assert env.get("sql_used") in (None, "")
    assert not env.get("reconfirm_accepted")
    text = str(env.get("text") or "")
    assert reason in _reasons(env) + " " + text, (reason, text, env.get("assumptions"))
    assert LEGACY_TEXT not in text
    assert rig.cortex.executed == [], "refused held SQL reached Cortex submit"
    assert rig.sql_submits() == [] and rig.ledger == []
    return text


# --- clause 1: the reproduction ---------------------------------------------------


def test_yes_on_a_held_wrong_join_abstains_unverified_join(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """On the parent commit this serves L2_VALIDATED with school_count 1."""
    rig = _held_rig(tmp_path, monkeypatch, minter)
    # It runs and returns a number: that is the danger.
    assert WRONG_FIGURE == 1 and VERIFIED_TRUTH == 3
    assert _oracle(rig.lake, WRONG_JOIN_SQL) == [{"school_count": WRONG_FIGURE}]
    token = rig.park(VERIFIED_Q, WRONG_JOIN_SQL)

    env = rig.yes(token)
    text = _assert_yes_abstains(
        env, rig, f"unverified_join:{DISTRICTS} x {SCHOOLS} joined on columns"
    )
    assert text == YES_ABSTAIN_TEXT
    assert env["answer_id"] == "ans_reconfirm_yes"
    # E9: nothing executed, so no figure may be stated.
    assert "school_count" not in text, text
    assert not re.search(r"\d", text), text
    assert rig.trace() == ExecutedTrace()


# --- clause 2: the verified link answers the truth -----------------------------------


def test_yes_on_held_sql_over_the_verified_link_answers_the_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)

    env = rig.yes(token)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert env["rows"] == [{"school_count": VERIFIED_TRUTH}]
    assert [v["value"] for v in env["values"]] == [float(VERIFIED_TRUTH)]
    assert f"school_count={VERIFIED_TRUTH}" in str(env.get("text") or ""), env.get("text")
    assert _multiset(env["rows"]) == _multiset(_oracle(rig.lake, VERIFIED_SQL))
    assert env["reconfirm_accepted"] is True
    assert env["provenance"] == "yes:definition:not_in_ontology"


# --- clause 4: Cortex executed it, and the ledger has it -----------------------------


def test_yes_serves_only_what_cortex_executed_and_the_ledger_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """On the parent commit: zero submits, zero ledger entries, a constant audit_id."""
    import dms_executor.sql_loop as sql_loop

    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    local_runs: list[str] = []
    inner = sql_loop.run_readonly

    def _spy(sql: str, *a: Any, **k: Any) -> Any:
        local_runs.append(sql)
        return inner(sql, *a, **k)

    monkeypatch.setattr(sql_loop, "run_readonly", _spy)
    rig.entry_id = "led_yes_entry_524"

    env = rig.yes(token)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == [{"school_count": VERIFIED_TRUTH}]
    assert f"school_count={VERIFIED_TRUTH}" in str(env.get("text") or ""), env.get("text")

    # Cortex executed exactly the held statement, under a signed manifest minted
    # for this session, granting this Space's tables.
    assert rig.cortex.executed == [VERIFIED_SQL]
    (req,) = rig.sql_submits()
    assert req.manifest.signature
    assert req.manifest.session_id and SESSION in str(req.manifest.session_id), req.manifest
    assert {SCHOOLS, DISTRICTS} <= {str(t).lower() for t in req.manifest.row_predicates}
    # One ledger entry, appended through Cortex, for that statement and that run.
    (entry,) = rig.ledger
    assert entry.payload["sql"] == VERIFIED_SQL
    assert entry.payload["run_id"] == "run_space_sql"
    assert entry.payload["space_id"] == rig.space_id
    # The envelope's audit_id is that entry, and so is the trace the route audits.
    assert env["audit_id"] == "led_yes_entry_524"
    assert env["audit_id"] != "ans_reconfirm_yes"
    trace = rig.trace()
    assert trace.statements == ((VERIFIED_SQL, 1),)
    assert trace.ledger == (("led_yes_entry_524", 1),)
    # No row came from a local cursor.
    assert VERIFIED_SQL not in local_runs, "the held SQL ran on a local cursor"


# --- clause 3: every gate of the first-generate door, one case per gate -------------
#
# name -> (question, held SQL, reason the gate gives). Each statement is parked by
# a real ask (def-validate reconfirms it before any later gate reads it), so the
# Yes is the first time the gate sees it. ``VERIFIED_TRUTH`` is never served.

E = od.ENROLL
QUALIFIER_Q = "How many schools are in the North district in 2024?"
GATE_REFUSES = {
    "space_join_rule": (
        VERIFIED_Q,
        WRONG_JOIN_SQL,
        f"unverified_join:{DISTRICTS} x {SCHOOLS} joined on columns",
    ),
    "broken_declared_link": (
        od.BROKEN_Q,
        od.BROKEN_SQL,
        f"ontology_unverified: fk_intact on {od.BROKEN_LINK}",
    ),
    "explain": (
        VERIFIED_Q,
        f"SELECT COUNT(no_such_column) AS n FROM {SCHOOLS}",
        "validate:explain:BinderException",
    ),
    "unhonored_qualifier": (
        QUALIFIER_Q,
        VERIFIED_SQL,
        "unhonored_qualifier:time_filter=year=2024",
    ),
}


@pytest.mark.parametrize("name", sorted(GATE_REFUSES))
def test_yes_is_refused_by_each_gate_with_that_gates_reason(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    question, sql, reason = GATE_REFUSES[name]
    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(question, sql)
    env = rig.yes(token)
    _assert_yes_abstains(env, rig, reason)
    assert rig.trace() == ExecutedTrace()


def test_yes_is_refused_by_the_text_numeric_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """A declared-numeric column that landed as text: MAX over it orders as text.

    The registry read is stubbed to flag ``students`` after the ask; the gate
    (``untyped_numeric_reason``) and everything around it are the real code.
    """
    import dms_executor.bronze as bronze

    rig = _held_rig(tmp_path, monkeypatch, minter)
    sql = f"SELECT MAX(students) AS top FROM {E}"
    assert _oracle(rig.lake, sql) == [{"top": "40"}]
    token = rig.park("What is the largest enrollment?", sql)
    monkeypatch.setattr(
        bronze, "untyped_numeric_columns", lambda tables, **_k: {E: {"students"}}
    )
    env = rig.yes(token)
    text = _assert_yes_abstains(env, rig, f"untyped_numeric:{E}.students")
    assert "40" not in text


# Held SQL that no ask would park: the pending entry is rewritten after the ask,
# which is what a corrupted or forged hold looks like at Yes. name -> (SQL,
# reason with def-validate on at Yes, reason with it off at Yes). With it on,
# def-validate reads the forged statement first: it abstains, or raises a
# reconfirm that is not the one the Yes answered, and that is refused too.
FORGED_HOLDS = {
    "multi_statement": (
        f"SELECT COUNT(*) AS n FROM {SCHOOLS}; SELECT COUNT(*) AS n FROM {DISTRICTS}",
        "reconfirm: not_in_ontology",
        "multi_statement",
    ),
    "not_a_read": (
        f"DELETE FROM {SCHOOLS}",
        "reconfirm: not_in_ontology",
        "unverified_join:not_one_read",
    ),
    "not_sql": ("SELEC nothing FRM", "sql_not_analyzable", "unverified_join:parse"),
    "table_function": (
        "SELECT * FROM read_csv('/etc/passwd')",
        "ungranted",
        "unverified_join:table_function",
    ),
    "ungranted_table": ("SELECT COUNT(*) AS n FROM shipments", "ungranted", "validate:ungranted"),
    "select_star": (f"SELECT * FROM {SCHOOLS}", "ungranted", "unhonored_qualifier"),
}


@pytest.mark.parametrize("def_validate_at_yes", ["1", "0"])
@pytest.mark.parametrize("name", sorted(FORGED_HOLDS))
def test_forged_hold_is_refused_by_the_door(
    name: str,
    def_validate_at_yes: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    import dms_executor.reconfirm as rc

    sql, with_def, without_def = FORGED_HOLDS[name]
    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    with rc._LOCK:
        rc._PENDING[token]["sql"] = sql
    monkeypatch.setenv("DMS_DEF_VALIDATE", def_validate_at_yes)
    env = rig.yes(token)
    _assert_yes_abstains(env, rig, with_def if def_validate_at_yes == "1" else without_def)
    assert rig.cortex.refused == []


def test_yes_answers_only_the_reconfirm_that_parked_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """A Yes to one definition is not a Yes to another: a changed code is not passed."""
    import dms_executor.reconfirm as rc

    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    with rc._LOCK:
        rc._PENDING[token]["reason_code"] = "undeclared_join"
    env = rig.yes(token)
    _assert_yes_abstains(env, rig, "not_in_ontology")

    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    with rc._LOCK:
        rc._PENDING[token]["definition"] = "some_other.definition"
    _assert_yes_abstains(rig.yes(token), rig, "not_in_ontology")


def test_extract_loop_door_gates_and_serves_held_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """C-LOOP-B returns before the first-generate branch: a Yes meets its check too."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, WRONG_JOIN_SQL)
    asked = len(rig.cortex.insights)
    env = rig.yes(token)
    _assert_yes_abstains(env, rig, "unverified_join")
    assert len(rig.cortex.insights) == asked, "a Yes asked the model to write SQL"

    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    env = rig.yes(token)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == [{"school_count": VERIFIED_TRUTH}]
    assert f"school_count={VERIFIED_TRUTH}" in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == [VERIFIED_SQL]
    assert len(rig.ledger) == 1 and env["audit_id"] == rig.entry_id


def test_yes_never_asks_a_model_and_never_reaches_the_contract_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    asked = len(rig.cortex.insights)

    def _no_ask(req: Any) -> Any:
        raise AssertionError("a Yes on held SQL reached the Cortex contract ask")

    monkeypatch.setattr(rig.cortex, "ask", _no_ask)
    # Whatever the generate leg would write now is not what the user confirmed.
    rig.cortex.payload = _gen(WRONG_JOIN_SQL)
    env = rig.yes(token)
    assert env["rows"] == [{"school_count": VERIFIED_TRUTH}]
    assert rig.cortex.executed == [VERIFIED_SQL]
    assert len(rig.cortex.insights) == asked


def test_door_serving_another_statement_is_not_a_served_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """The user confirmed one statement. A different one, even a gated one, is not it."""
    import dms_executor as de

    monkeypatch.setenv("DMS_VQ_RAG", "1")
    rig = _held_rig(tmp_path, monkeypatch, minter)
    saved = _saved_examples(monkeypatch)
    held = f"SELECT COUNT(*) AS school_count FROM {SCHOOLS}"
    token = rig.park("How many schools are there?", held)
    second = rig.park("How many schools are there?", held)
    # Stand-in for any lane of the door that serves SQL of its own.
    monkeypatch.setattr(de, "_insights_compute_seam", lambda *_a, **_k: _gen(VERIFIED_SQL))
    env = rig.yes(token)
    # With def-validate on, the other statement raises a reconfirm nobody answered.
    assert env["badge"] == "ABSTAIN" and env["rows"] == []
    assert "reconfirm: not_in_ontology" in _reasons(env), env.get("assumptions")
    assert rig.cortex.executed == [] and saved == []

    # With it off the door gates and serves that other statement through Cortex.
    # It is still not the statement the user confirmed.
    monkeypatch.setenv("DMS_DEF_VALIDATE", "0")
    env = rig.yes(second)
    assert rig.cortex.executed == [VERIFIED_SQL]
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert "held_sql_not_served" in _reasons(env), env.get("assumptions")
    assert not env.get("reconfirm_accepted")
    assert str(VERIFIED_TRUTH) not in str(env.get("text") or "")
    assert saved == []


# --- clause 5: Cortex down is an abstain, never a local run ---------------------------


def _spy_local_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import dms_executor.sql_loop as sql_loop

    ran: list[str] = []
    inner = sql_loop.run_readonly

    def _spy(sql: str, *a: Any, **k: Any) -> Any:
        ran.append(sql)
        return inner(sql, *a, **k)

    monkeypatch.setattr(sql_loop, "run_readonly", _spy)
    return ran


@pytest.mark.parametrize("down", ["submit", "bind_and_submit"])
def test_cortex_unreachable_abstains_named_with_no_local_run(
    down: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    import httpx

    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    local_runs = _spy_local_runs(monkeypatch)
    reached: list[Any] = []

    def _down(req: Any) -> Any:
        reached.append(req)
        raise httpx.ConnectError("cortex unreachable")

    monkeypatch.setattr(rig.cortex, "submit", _down)
    if down == "bind_and_submit":
        rig.exe._bound_sessions.clear()
    env = rig.yes(token)
    text = _assert_yes_abstains(env, rig, "submit_failed")
    assert reached, "the Yes never tried Cortex"
    assert VERIFIED_SQL not in local_runs, "Cortex was down and the held SQL ran locally"
    assert str(VERIFIED_TRUTH) not in text and "school_count" not in text


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("raises", "ledger_append_failed"),
        ("no_entry", "ledger_entry_missing"),
        ("no_hash", "ledger_hash_missing"),
    ],
)
def test_no_ledger_entry_means_no_served_figure(
    fault: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """Cortex executed the statement, the ledger did not take it: nothing is served."""
    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)

    def _ledger(req: LedgerAppendRequest) -> LedgerAppendResponse:
        if fault == "raises":
            raise ConnectionError("ledger unreachable")
        if fault == "no_entry":
            return LedgerAppendResponse(entry_id=" ", hash="hash_x")
        return LedgerAppendResponse(entry_id="led_x", hash="")

    monkeypatch.setattr(rig.cortex, "ledger_append", _ledger)
    env = rig.yes(token)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert not env.get("drillthrough_token") and not env.get("reconfirm_accepted")
    text = str(env.get("text") or "")
    assert reason in _reasons(env), env.get("assumptions")
    assert str(VERIFIED_TRUTH) not in text and "school_count" not in text


# --- clause 6: the grants are read at Yes ---------------------------------------------


class _Revoked:
    """The session store, with one table's grant removed from this Space."""

    def __init__(self, inner: Any, table: str) -> None:
        from dms_executor.demo_grants import source_id_for

        self._inner = inner
        self._gone = source_id_for(table)

    def list_space_source_ids(self, space_id: str) -> list[Any]:
        return [s for s in self._inner.list_space_source_ids(space_id) if s != self._gone]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_grant_removed_between_ask_and_yes_abstains_ungranted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    from dms_executor.reconfirm import peek_pending

    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    assert DISTRICTS in rig.exe.grantable_tables(space_id=rig.space_id)
    rig.exe._session_store = _Revoked(rig.exe._session_store, DISTRICTS)
    assert DISTRICTS not in rig.exe.grantable_tables(space_id=rig.space_id)
    # The parked snapshot still lists it. It is not what the Yes reads.
    pending = peek_pending(token)
    assert pending is not None and DISTRICTS in pending["grantable"]

    env = rig.yes(token)
    text = _assert_yes_abstains(env, rig, "ungranted")
    assert rig.cortex.refused == [], "DMS left the refusal to Cortex"
    assert str(VERIFIED_TRUTH) not in text and "school_count" not in text


def test_parked_grant_list_is_not_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """A forged parked list cannot grant a demo table this Space was never given."""
    import dms_executor.reconfirm as rc

    rig = _held_rig(tmp_path, monkeypatch, minter)
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    sql = "SELECT COUNT(*) AS n FROM shipments"
    assert _oracle(rig.lake, sql)[0]["n"] > 0
    with rc._LOCK:
        rc._PENDING[token]["sql"] = sql
        rc._PENDING[token]["grantable"] = ["shipments"]
    _assert_yes_abstains(rig.yes(token), rig, "ungranted")


# --- clause 7: an abstained Yes stores no example --------------------------------------


def _saved_examples(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import dms_executor as de

    saved: list[dict[str, Any]] = []
    inner = de.maybe_save_checked_sql

    def _wrapped(**kwargs: Any) -> Any:
        got = inner(**kwargs)
        saved.append({**kwargs, "stored": got})
        return got

    monkeypatch.setattr(de, "maybe_save_checked_sql", _wrapped)
    return saved


def test_abstained_yes_stores_no_example_and_a_served_yes_stores_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    from dms_executor.verified_queries import list_verified_queries

    monkeypatch.setenv("DMS_VQ_RAG", "1")
    rig = _held_rig(tmp_path, monkeypatch, minter)
    saved = _saved_examples(monkeypatch)

    token = rig.park(VERIFIED_Q, WRONG_JOIN_SQL)
    _assert_yes_abstains(rig.yes(token), rig, "unverified_join")
    assert saved == [], "an abstained Yes offered its SQL to the example store"
    assert list_verified_queries(space_id=rig.space_id, path=rig.lake) == []

    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    env = rig.yes(token)
    assert env["rows"] == [{"school_count": VERIFIED_TRUTH}]
    (offer,) = saved
    assert offer["sql"] == VERIFIED_SQL and offer["abstained"] is False
    assert offer["provenance"] == "yes:definition:not_in_ontology"
    # Offered with the grants read at the Yes, not the parked list.
    assert offer["grantable"] == set(rig.exe.grantable_tables(space_id=rig.space_id))
    rows = list_verified_queries(space_id=rig.space_id, path=rig.lake)
    assert [r["sql"] for r in rows] == ([VERIFIED_SQL] if offer["stored"] else [])


# --- clause 8: a Space with no ontology keeps the CONNECT-ASK-03 behaviour --------------


def test_no_ontology_space_join_abstains_and_one_table_serves_through_cortex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    import test_connect_ask_03 as ca3

    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    file_rig = ca3._file_space(tmp_path, monkeypatch, minter)
    ledger: list[LedgerAppendRequest] = []

    def _ledger(req: LedgerAppendRequest) -> LedgerAppendResponse:
        ledger.append(req)
        return LedgerAppendResponse(entry_id="led_file_space", hash="hash_file_space_x")

    monkeypatch.setattr(file_rig.cortex, "ledger_append", _ledger)
    token = ca3._parked_join(file_rig)
    env = file_rig.confirm(token, yes=True)
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    assert ca3.NO_ONTOLOGY in _reasons(env), env.get("assumptions")
    for figure in ca3.JOIN_FIGURES:
        assert not re.search(rf"\b{figure}\b", str(env.get("text") or "")), env.get("text")
    assert file_rig.cortex.executed == [] and ledger == []

    offer = file_rig.ask(ca3.ONE_TABLE_Q, ca3._gen(ca3.ONE_TABLE_SQL))
    assert offer["badge"] == "ABSTAIN" and offer["rows"] == []
    env = file_rig.confirm(offer["reconfirm"]["token"], yes=True)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == [{"total": ca3.ONE_TABLE_TRUTH}]
    assert f"total={ca3.ONE_TABLE_TRUTH}" in str(env.get("text") or ""), env.get("text")
    assert file_rig.cortex.executed == [ca3.ONE_TABLE_SQL]
    assert len(ledger) == 1 and env["audit_id"] == "led_file_space"


# --- clause 9: flag off, and the demo Space, are the parent's envelopes -----------------


def test_run_held_flag_unset_never_runs_the_held_statement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """Yes re-asks the question on the normal ladder, as on the parent commit."""
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    rig = _Rig(od._space(tmp_path, monkeypatch, minter))
    called: list[Any] = []
    monkeypatch.setattr(rig.exe, "_run_held_sql", lambda pending: called.append(pending))
    token = rig.park(VERIFIED_Q, VERIFIED_SQL)
    asked = len(rig.cortex.insights)

    env = rig.yes(token)
    assert called == []
    assert len(rig.cortex.insights) == asked + 1, "the question was not asked again"
    # The same def-validate reconfirm comes back; nothing ran.
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    assert env["reconfirm"]["definition"] == "public_districts.district_id"
    assert rig.cortex.executed == [] and rig.ledger == []


def test_demo_space_yes_is_the_parent_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Clause 9. The demo Space keeps its local run and its wording, unchanged."""
    import test_reconfirm_yes_01 as ry
    from dms_core.control_plane.onto_data import reset_ontology_data_store

    reset_ontology_data_store()
    try:
        monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
        offered, path = ry._offered(monkeypatch, tmp_path)
        ran: list[str] = []
        ry._patch_run(monkeypatch, ran)
        env = ry._executor(path).confirm_reconfirm(offered["reconfirm"]["token"], True)
        assert_envelope_valid(env)
        assert ran == [ry._JOIN]
        assert env["text"] == LEGACY_TEXT and env["badge"] == "L2_VALIDATED"
        assert env["answer_id"] == "ans_reconfirm_yes" and env["rows"]
        assert env["provenance"] == "yes:definition:undeclared_join"
    finally:
        reset_ontology_data_store()
