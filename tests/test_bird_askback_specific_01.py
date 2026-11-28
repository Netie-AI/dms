"""BIRD-ASKBACK-GENERIC-01. Flag default off. Synthetic schema only.

Off, the ask-back prompt, the envelope text, and the route stay the canned
card. On, a silent generator does not offer that card. Two runnable readings
show their SQL and rows. Yes re-gates the reading SQL and stores the asked
question.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

_Q = "How many lots are on hand"
_Q2 = "How many shipments left the yard"
_GENERIC = "Show one measure for this Space."
_OFF_TEXT = (
    "I need one of these readings: "
    "Show one measure for this Space. | "
    "Break that measure down by one granted column."
)
_SKU = "SELECT sku FROM lots"
_QTY = "SELECT qty FROM lots"
_WRITE = "INSERT INTO lots VALUES ('c', 3)"
_CANNED = "Show one measure for this Space."


def _old_readings(question: str) -> str:
    return (
        "Reply with a JSON object whose key readings is an array of "
        "2 or 3 concrete questions this schema can answer.\n"
        f"Question:\n{question}"
    )


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    from dms_executor.reconfirm import _reset_reconfirm
    from dms_executor.scale_miss import bind_turn, reset_turn

    _reset_reconfirm()
    token = bind_turn(None)
    yield
    reset_turn(token)
    _reset_reconfirm()


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "lots.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE lots (sku VARCHAR, qty INTEGER)")
    con.execute("INSERT INTO lots VALUES ('a', 1), ('b', 2)")
    con.close()
    return path


def _turn(db: Path, question: str, compute: Any) -> Any:
    from dms_executor.scale_miss import Turn

    turn = Turn()
    turn.question = question
    turn.compute = compute
    turn.warehouse = db
    turn.grantable = {"lots"}
    turn.dialect = "duckdb"
    return turn


def _ask(db: Path, question: str, compute: Any) -> dict[str, Any]:
    from dms_executor.scale_miss import bind_turn, empty_sql_askback, reset_turn

    token = bind_turn(_turn(db, question, compute))
    try:
        return empty_sql_askback(question)
    finally:
        reset_turn(token)


def _view(env: dict[str, Any]) -> dict[str, Any]:
    block = dict(env.get("reconfirm") or {})
    block.pop("token", None)
    return {
        "route": env.get("route"),
        "served_route": env.get("served_route"),
        "text": env.get("text"),
        "badge": env.get("badge"),
        "sql_used": env.get("sql_used"),
        "reconfirm": block,
        "askback_specific": env.get("askback_specific"),
    }


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv("DMS_ASKBACK_SPECIFIC", "1")
    else:
        monkeypatch.delenv("DMS_ASKBACK_SPECIFIC", raising=False)


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.askback_specific import enabled

    monkeypatch.delenv("DMS_ASKBACK_SPECIFIC", raising=False)
    assert enabled() is False
    monkeypatch.setenv("DMS_ASKBACK_SPECIFIC", "true")
    assert enabled() is False
    monkeypatch.setenv("DMS_ASKBACK_SPECIFIC", "1")
    assert enabled() is True


def test_flag_off_prompt_envelope_and_route_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _flag(monkeypatch, False)
    db = _db(tmp_path)
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return {}

    first = _ask(db, _Q, compute)
    second = _ask(db, _Q, compute)
    assert calls[0]["sql_vote_prompt"] == _old_readings(_Q)
    assert json.dumps(calls[0], sort_keys=True) == json.dumps(calls[1], sort_keys=True)
    assert first["text"] == _OFF_TEXT
    assert first["route"] == "generated"
    assert first.get("served_route") == "abstain:generative"
    assert first["reconfirm"]["suggested_question"] == _GENERIC
    assert _view(first) == _view(second)
    assert "askback_specific" not in first


def test_silent_generator_offers_no_generic_reading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor.reconfirm import apply_reconfirm

    _flag(monkeypatch, True)
    db = _db(tmp_path)
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return {}

    env = _ask(db, _Q, compute)
    blob = json.dumps(env)
    assert _GENERIC not in blob
    assert "Break that measure down by one granted column." not in blob
    assert env.get("reconfirm") is None
    assert env["askback_specific"] == "closed"
    assert env["route"] == "abstain"
    assert calls and calls[0]["route_hint"] == "reason"
    assert _Q in calls[0]["sql_vote_prompt"]
    assert "lots.sku" in calls[0]["sql_vote_prompt"]
    legs = env["generate_legs"]["legs"]
    assert legs[0]["returned"] == "nothing"
    assert legs[0]["outcome"] == "empty_text"

    def _refuse(_prompt: str) -> str:
        raise AssertionError("a closed card must not ask for a closest question")

    offered = apply_reconfirm(
        env,
        question=_Q,
        space_id=None,
        session_id=None,
        tables=None,
        ask_path=None,
        ontology="",
        schema="",
        complete=_refuse,
    )
    assert offered.get("reconfirm") is None
    assert offered.get("askback_specific") == "closed"


def test_two_readings_show_sql_and_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _flag(monkeypatch, True)
    db = _db(tmp_path)
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return {
            "readings": [
                {"text": f"Sku list for {_Q}", "sql": _SKU},
                {"text": f"Quantities for {_Q}", "sql": _QTY},
                {"text": _CANNED, "sql": _SKU},
                {"text": "Add a lot", "sql": _WRITE},
            ]
        }

    env = _ask(db, _Q, compute)
    readings = env["reconfirm"]["readings"]
    assert [item["text"] for item in readings] == [
        f"Sku list for {_Q}",
        f"Quantities for {_Q}",
    ]
    assert readings[0]["sql"] == _SKU
    assert readings[1]["sql"] == _QTY
    assert readings[0]["rows"] == [{"sku": "a"}, {"sku": "b"}]
    assert readings[0]["row_count"] == 2
    assert readings[1]["rows"] == [{"qty": 1}, {"qty": 2}]
    assert readings[1]["row_count"] == 2
    assert _GENERIC not in json.dumps(env)
    assert env["reconfirm"]["suggested_question"] == f"Sku list for {_Q}"
    assert env["reconfirm"]["question"] == _Q
    assert calls[0]["route_hint"] == "reason"


def test_two_questions_do_not_share_a_suggestion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _flag(monkeypatch, True)
    db = _db(tmp_path)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        question = str(ctx["sql_vote_prompt"]).split("Question:\n", 1)[1]
        return {"readings": [{"text": f"Count rows for {question}", "sql": _SKU}]}

    first = _ask(db, _Q, compute)
    second = _ask(db, _Q2, compute)
    left = first["reconfirm"]["suggested_question"]
    right = second["reconfirm"]["suggested_question"]
    assert left != right
    assert _Q in left
    assert _Q2 in right
    assert _GENERIC not in left
    assert _GENERIC not in right


def test_yes_regates_and_stores_the_asked_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor import Executor
    from dms_executor.reconfirm import apply_reconfirm

    _flag(monkeypatch, True)
    db = _db(tmp_path)
    ran: list[str] = []
    saved: list[dict[str, Any]] = []
    asked: list[str] = []

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return {"readings": [{"text": f"Sku list for {_Q}", "sql": _SKU}]}

    def _refuse(_prompt: str) -> str:
        raise AssertionError("the reading is already on the envelope")

    def offer() -> dict[str, Any]:
        env = _ask(db, _Q, compute)
        return apply_reconfirm(
            env,
            question=_Q,
            space_id=None,
            session_id=None,
            tables=["lots"],
            ask_path=None,
            ontology="",
            schema="",
            complete=_refuse,
        )

    blocked = offer()
    token = blocked["reconfirm"]["token"]
    assert blocked["reconfirm"]["readings"][0]["sql"] == _SKU
    assert blocked["reconfirm"]["question"] == _Q

    def gate(*_args: Any, **kwargs: Any) -> str:
        asked.append(str(kwargs.get("question") or ""))
        return "gate_blocked"

    monkeypatch.setattr("dms_executor.generative_ask.validated_sql_reason", gate)

    from dms_executor.sql_loop import run_readonly as real_run

    def _run(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return real_run(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", _run)

    def _save(**kwargs: Any) -> dict[str, Any]:
        saved.append(kwargs)
        return {"asset_id": "vq_test"}

    monkeypatch.setattr("dms_executor.maybe_save_checked_sql", _save)
    executor = Executor(warehouse_path=db, dialect="duckdb")
    executor.live_ask = _live_must_not_run  # type: ignore[method-assign]
    env = executor.confirm_reconfirm(token, True)
    assert ran == []
    assert saved == []
    assert asked == [_Q]
    assert env["abstained"] is True
    assert env.get("sql_used") in (None, "")
    assert env.get("reconfirm_accepted") is not True

    passed = offer()
    monkeypatch.setattr(
        "dms_executor.generative_ask.validated_sql_reason",
        lambda *_args, **_kwargs: None,
    )
    ran.clear()
    served = executor.confirm_reconfirm(passed["reconfirm"]["token"], True)
    assert ran == [_SKU]
    assert served["abstained"] is False
    assert served["sql_used"] == _SKU
    assert served["reconfirm_accepted"] is True
    assert served["provenance"] == "yes:reconfirm"
    assert saved and saved[0]["question"] == _Q
    assert saved[0]["sql"] == _SKU
    assert saved[0]["question"] != _GENERIC
    assert _GENERIC not in saved[0]["question"]


def _live_must_not_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("Yes must run the reading SQL, not a new question")


def test_flag_on_generate_prompt_names_the_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor.scale_miss import begin, bind_turn, reset_turn
    from dms_executor.sql_vote import reading_prompt

    db = _db(tmp_path)
    calls: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return {}

    _flag(monkeypatch, False)
    token = bind_turn(None)
    try:
        begin(
            question=_Q,
            compute=compute,
            ontology=None,
            warehouse=db,
            grantable={"lots"},
            dialect="duckdb",
            space_id=None,
            session_id=None,
        )
    finally:
        reset_turn(token)
    assert calls[0]["sql_vote_prompt"] == reading_prompt(_Q, reading=1)
    calls.clear()
    _flag(monkeypatch, True)
    token = bind_turn(None)
    try:
        begin(
            question=_Q,
            compute=compute,
            ontology=None,
            warehouse=db,
            grantable={"lots"},
            dialect="duckdb",
            space_id=None,
            session_id=None,
        )
    finally:
        reset_turn(token)
    prompt = calls[0]["sql_vote_prompt"]
    assert prompt != reading_prompt(_Q, reading=1)
    assert _Q in prompt
    assert "lots.sku" in prompt
    assert calls[0].get("route_hint") not in {"gpt", "claude", "openai"}
