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
