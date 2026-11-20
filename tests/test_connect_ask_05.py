"""CONNECT-ASK-05 (dms#528): a join check that raises abstains, it does not 503.

Before: in a Space that has an ontology, the three guards called the join rule
with no handler. An exception from the check left the ask, and the route
answered HTTP 503 ``live_ask_failed`` with the exception text, or a demo answer
when ``DMS_DEMO_FALLBACK=1``. A Space with no ontology already abstained
``unverified_join:check_unavailable`` for the same fault (dms#520).

Here the Space of ``tests/test_onto_derive_01.py`` (a SQL source, one verified
link) is asked with SQL that the real join rule ALLOWS and that would be served
(``school_count=3``). The test makes the join check raise. Every case posts
``POST /v1/chat/ask`` and asserts the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows) and the raw response
body. No live model.

The Yes door (clause 4) is not here: held SQL does not meet the join rule in a
Space with an ontology until dms#524, and that test is committed with #524.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import dms_executor.generative_ask as ga
import dms_executor.space_ontology as so
import pytest
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_api.settings import Settings, get_settings
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_registry import (
    SERVING_ERRORS,
    ServingDeadlineExceeded,
    ServingDeadlineReserve,
    ServingLeaseCap,
    ServingLeaseQueueFull,
    ServingWaitCancelled,
)
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import load_space_ontology, set_ontology_store

CHECK_UNAVAILABLE = "unverified_join:check_unavailable"
SECRET = "join analyser raised"
DOORS = ["first_generate", "extract_loop", "contract_ask"]
#: The figure the allowed SQL would serve. E9: an abstain must not state it.
SERVED_FIGURE = "3"
CONTRACT_ANSWER = "There are 3 schools in the North district."


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return od.make_minter(monkeypatch)


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
        "DMS_DEMO_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()


def _raiser(exc: BaseException) -> Any:
    def _raise(*_a: Any, **_k: Any) -> str | None:
        raise exc

    return _raise


def _break_join_check(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Every guard resolves the join rule through one of these two names."""
    monkeypatch.setattr(ga, "unverified_join_reason", _raiser(exc))
    monkeypatch.setattr(so, "unverified_join_reason", _raiser(exc))


def _demo_fallback_on(rig: Any) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=True,
        dms_harness_ask_paths=True,
    )
    rig.client.app.dependency_overrides[get_settings] = lambda: settings


def _rig(door: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Any:
    if door == "extract_loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    rig = od._space(tmp_path, monkeypatch, minter)
    assert load_space_ontology(rig.space_id) is not None, "the Space must have an ontology"
    # The danger: the real rule allows this SQL, and it returns a figure.
    assert od._oracle(rig.lake, od.VERIFIED_SQL) == [{"school_count": 3}]
    return rig


def _post(rig: Any, door: str, monkeypatch: pytest.MonkeyPatch, sql: str = od.VERIFIED_SQL) -> Any:
    """Send ``sql`` through ``door``. Returns the raw HTTP response."""
    if door == "contract_ask":

        def _ask(req: Any) -> AskResponse:
            rig.cortex.asks.append(req)
            return AskResponse(
                answer=CONTRACT_ANSWER,
                abstained=False,
                badge="generated",
                route="generated",
                sql_used=sql,
                rows=[{"school_count": 3}],
            )

        monkeypatch.setattr(rig.cortex, "ask", _ask)
        rig.cortex.payload = {}
    else:
        rig.cortex.payload = {"query_sql": sql, "plan_source": "ontology_plan"}
    r = rig.client.post(
        "/v1/chat/ask",
        json={
            "question": od.VERIFIED_Q,
            "session_id": f"ses_connect_ask_05_{door}",
            "space_id": rig.space_id,
        },
    )
    if door == "contract_ask":
        assert rig.cortex.asks, "the contract ask was not reached"
    else:
        assert rig.cortex.insights, "the generate stub was not asked"
    return r


def _assert_check_unavailable(r: Any, rig: Any, secret: str = SECRET) -> dict[str, Any]:
    """Clauses 1 to 3 on what the customer receives."""
    assert r.status_code == 200, (r.status_code, r.text)
    env: dict[str, Any] = r.json()
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert not env.get("drillthrough_token")
    assert env.get("sql_used") in (None, "")
    text = str(env.get("text") or "")
    assert CHECK_UNAVAILABLE in od._reasons(env) + text, (text, env.get("assumptions"))
    # E9: nothing executed, so the figure the SQL would serve is not stated.
    assert not re.search(rf"\b{SERVED_FIGURE}\b", text), text
    assert "school_count" not in text, text
    assert rig.cortex.executed == [], "the unchecked SQL reached Cortex submit"
    # Clause 2: no exception text anywhere in the response.
    assert secret not in r.text, r.text
    # Clause 3: not a demo answer, in any costume.
    assert env.get("demo_fallback_used") is False, env
    assert "fallback" not in od._reasons(env).lower() + text.lower(), env
    assert "demo" not in str(env.get("answer_id")) + od._reasons(env).lower() + text.lower(), env
    assert "live_ask_failed" not in r.text
    return env


# --- clauses 1 and 2: the three doors ---------------------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_join_check_that_raises_abstains_check_unavailable(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """On the parent this is HTTP 503 ``live_ask_failed`` with the exception text."""
    rig = _rig(door, tmp_path, monkeypatch, minter)
    _break_join_check(monkeypatch, RuntimeError(SECRET))
    with caplog.at_level(logging.WARNING):
        r = _post(rig, door, monkeypatch)
    _assert_check_unavailable(r, rig)
    # Clause 2: the log names the exception class and never its text.
    assert "RuntimeError" in caplog.text, caplog.text
    assert SECRET not in caplog.text, caplog.text


# --- clause 3: the demo fallback is not a way out ---------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_demo_fallback_on_gives_the_same_abstain(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """On the parent this is a demo answer for a customer Space (rule 11)."""
    rig = _rig(door, tmp_path, monkeypatch, minter)
    _demo_fallback_on(rig)
    _break_join_check(monkeypatch, RuntimeError(SECRET))
    r = _post(rig, door, monkeypatch)
    _assert_check_unavailable(r, rig)


# --- clause 5: a named refusal stays named ----------------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_named_refusal_from_the_real_rule_is_not_flattened(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    rig = _rig(door, tmp_path, monkeypatch, minter)
    r = _post(rig, door, monkeypatch, sql=od.WRONG_JOIN_SQL)
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    gap = "loop_exhausted:checker:unverified_join" if door == "extract_loop" else "unverified_join"
    text = od._assert_abstain(env, gap)
    named = "unverified_join:bronze.public_districts x bronze.public_schools joined on columns"
    assert named in od._reasons(env) + text, (text, env.get("assumptions"))
    assert CHECK_UNAVAILABLE not in r.text, r.text
    assert rig.cortex.executed == []


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("why", ["parse", "scope", "statements", "unanalysable", "fan_trap (x)"])
def test_reason_the_check_returns_is_reported_exactly(
    why: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """The check answered (``_Refuse``, parse, scope): its reason is the reason."""
    reason = f"unverified_join:{why}"
    rig = _rig(door, tmp_path, monkeypatch, minter)
    monkeypatch.setattr(ga, "unverified_join_reason", lambda *_a, **_k: reason)
    monkeypatch.setattr(so, "unverified_join_reason", lambda *_a, **_k: reason)
    r = _post(rig, door, monkeypatch)
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    gap = "loop_exhausted:checker:unverified_join" if door == "extract_loop" else "unverified_join"
    text = od._assert_abstain(env, gap)
    assert reason in od._reasons(env) + text, (text, env.get("assumptions"))
    assert CHECK_UNAVAILABLE not in r.text, r.text
    assert rig.cortex.executed == []


@pytest.mark.parametrize("door", DOORS)
def test_check_that_passes_still_serves_the_truth(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """The handler does not refuse work the rule allows (as on the parent)."""
    rig = _rig(door, tmp_path, monkeypatch, minter)
    r = _post(rig, door, monkeypatch)
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert CHECK_UNAVAILABLE not in r.text, r.text
    if door == "contract_ask":
        # Parent behaviour: the rule passed, the contract answer is not refused
        # for its join. Whatever serves next is not this ticket's.
        assert "unverified_join" not in r.text, r.text
        return
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert od._multiset(env["rows"]) == od._multiset([{"school_count": 3}])
    assert "school_count=3" in str(env.get("text") or ""), env.get("text")
    assert rig.cortex.executed == [od.VERIFIED_SQL]


# --- clause 7: lease errors, deadlines and stops are not join abstains ------------

_SERVING = {
    "lease_cap": lambda: ServingLeaseCap(4, 0.25),
    "queue_full": lambda: ServingLeaseQueueFull(8),
    "deadline_exceeded": ServingDeadlineExceeded,
    "deadline_reserve": ServingDeadlineReserve,
    "wait_cancelled": ServingWaitCancelled,
}


def test_every_serving_error_is_covered() -> None:
    assert {type(make()) for make in _SERVING.values()} == set(SERVING_ERRORS)


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(_SERVING))
def test_serving_error_reaches_the_ask_boundary(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """A lease miss, a deadline and a stop keep their own name (as on the parent)."""
    exc = _SERVING[name]()
    rig = _rig(door, tmp_path, monkeypatch, minter)
    _break_join_check(monkeypatch, exc)
    r = _post(rig, door, monkeypatch)
    assert "unverified_join" not in r.text, r.text
    assert rig.cortex.executed == []
    if isinstance(exc, ServingWaitCancelled):
        # The ask boundary has no envelope for a dropped waiter: it leaves the
        # ask as an error, exactly as on the parent.
        assert r.status_code == 503, (r.status_code, r.text)
        assert r.json()["detail"]["code"] == "live_ask_failed", r.text
        return
    assert r.status_code == 200, (r.status_code, r.text)
    env = r.json()
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert exc.code in str(env.get("text") or "") + od._reasons(env), env  # type: ignore[attr-defined]


# --- adversary round: other exception types, hostile exceptions ---------------------


class _HostileStr(RuntimeError):
    """An exception whose text cannot be rendered. The handler must not touch it."""

    def __str__(self) -> str:
        raise ValueError("str() of the exception was called")


class _LeaseCapChild(ServingLeaseCap):
    """A subclass keeps the lease code: it is still a lease error."""


def _other_exceptions() -> dict[str, Any]:
    import duckdb
    import sqlglot.errors

    return {
        "key_error": lambda: KeyError(SECRET),
        "recursion": lambda: RecursionError(SECRET),
        "memory": lambda: MemoryError(SECRET),
        "assertion": lambda: AssertionError(SECRET),
        "sqlglot_parse": lambda: sqlglot.errors.ParseError(SECRET),
        "duckdb": lambda: duckdb.Error(SECRET),
        "hostile_str": lambda: _HostileStr(SECRET),
    }


_OTHER = _other_exceptions()


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("name", sorted(_OTHER))
def test_any_other_exception_from_the_check_abstains(
    name: str,
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exc = _OTHER[name]()
    rig = _rig(door, tmp_path, monkeypatch, minter)
    _break_join_check(monkeypatch, exc)
    with caplog.at_level(logging.WARNING):
        r = _post(rig, door, monkeypatch)
    _assert_check_unavailable(r, rig)
    assert type(exc).__name__ in caplog.text, caplog.text
    assert SECRET not in caplog.text, caplog.text


@pytest.mark.parametrize("door", DOORS)
def test_lease_error_subclass_keeps_the_lease_code(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    exc = _LeaseCapChild(4, 0.25)
    rig = _rig(door, tmp_path, monkeypatch, minter)
    _break_join_check(monkeypatch, exc)
    r = _post(rig, door, monkeypatch)
    assert r.status_code == 200, (r.status_code, r.text)
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is True and env["rows"] == [] and env["values"] == []
    assert exc.code in str(env.get("text") or "") + od._reasons(env), env
    assert CHECK_UNAVAILABLE not in r.text, r.text
    assert rig.cortex.executed == []
