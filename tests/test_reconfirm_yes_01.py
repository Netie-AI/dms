"""RECONFIRM-YES-01. No skip, no xfail. CI runs this file inside ``pytest tests/``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.onto_data import reset_ontology_data_store

_QUESTION = "What is the order amount?"
_JOIN = "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    from dms_executor.reconfirm import _reset_reconfirm

    reset_ontology_data_store()
    _reset_reconfirm()
    yield
    reset_ontology_data_store()
    _reset_reconfirm()


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.reconfirm import reconfirm_run_held_enabled

    monkeypatch.delenv("DMS_RECONFIRM_RUN_HELD", raising=False)
    assert reconfirm_run_held_enabled() is False


def test_yes_executes_held_sql_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    offered, path = _offered(monkeypatch, tmp_path)
    token = offered["reconfirm"]["token"]
    assert "hold_id" not in offered["reconfirm"]
    assert offered.get("sql_used") in (None, "")
    assert _JOIN not in str(offered.get("reconfirm"))
    pending = _pending(token)
    assert pending["sql"] == _JOIN
    assert set(pending["grantable"]) == {"orders", "customers"}
    assert "orders.customer_id" in pending["definition"]
    assert pending["reason_code"] == "undeclared_join"
    assert "rows" not in pending

    ran: list[str] = []
    saved: list[dict[str, Any]] = []
    _patch_run(monkeypatch, ran)
    _patch_save(monkeypatch, saved)
    executor = _executor(path)
    env = executor.confirm_reconfirm(token, True)
    assert ran == [_JOIN]
    assert "reconfirm" not in env
    assert env["reconfirm_accepted"] is True
    assert env["provenance"] == "yes:definition:undeclared_join"
    assert env["abstained"] is False
    assert env["rows"]
    assert saved and saved[0]["provenance"] == "yes:definition:undeclared_join"
    assert saved[0].get("abstained") is False
    with pytest.raises(KeyError):
        executor.confirm_reconfirm(token, True)


def test_ungranted_held_sql_abstains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor import reconfirm as rc

    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    offered, path = _offered(monkeypatch, tmp_path)
    token = offered["reconfirm"]["token"]
    with rc._LOCK:
        rc._PENDING[token]["grantable"] = []
    ran: list[str] = []
    _patch_run(monkeypatch, ran)
    env = _executor(path).confirm_reconfirm(token, True)
    assert ran == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "reconfirm" not in env
    assert env.get("reconfirm_accepted") is not True
    assert env.get("sql_used") in (None, "")


def test_flag_off_keeps_reask(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from dms_executor.abstain import build_abstain

    monkeypatch.delenv("DMS_RECONFIRM_RUN_HELD", raising=False)
    offered, path = _offered(monkeypatch, tmp_path)
    token = offered["reconfirm"]["token"]
    seen: list[str] = []
    ran: list[str] = []
    _patch_run(monkeypatch, ran)
    executor = _executor(path)

    def reask(question: str, **_kwargs: Any) -> dict[str, Any]:
        seen.append(question)
        return build_abstain(
            reason="unknown_measure",
            question=question,
            stage="test",
            answer_id="ans_reask",
            text="reasked",
            abstain_reason="unknown_measure",
            assumptions=["GEN-01: unknown_measure"],
            route="abstain",
            ask_mode="live",
        )

    executor.live_ask = reask  # type: ignore[method-assign]
    env = executor.confirm_reconfirm(token, True)
    assert seen == [_QUESTION]
    assert ran == []
    assert env.get("provenance") != "yes:definition:undeclared_join"


def test_no_says_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    offered, path = _offered(monkeypatch, tmp_path)
    env = _executor(path).confirm_reconfirm(offered["reconfirm"]["token"], False)
    assert env["text"] == "not found in the database"
    assert env["abstained"] is True
    assert "reconfirm" not in env


def _offered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    from test_def_validate_01 import _JOIN as join_sql
    from test_def_validate_01 import _approve, _ask  # noqa: I001

    assert join_sql == _JOIN
    _approve(measure=True, pii=False)
    env, submitted = _ask(monkeypatch, tmp_path, _JOIN, flag="1")
    assert env is not None
    assert submitted == []
    from dms_executor.reconfirm import apply_reconfirm

    offered = apply_reconfirm(
        env,
        question=_QUESTION,
        space_id=None,
        session_id=None,
        tables=None,
        ask_path=None,
        ontology="",
        schema="",
        complete=_refuse_model,
    )
    return offered, tmp_path / "def_validate.duckdb"


def _refuse_model(_prompt: str) -> str:
    raise AssertionError("the closest question is already on the envelope")


def _pending(token: str) -> dict[str, Any]:
    from dms_executor.reconfirm import peek_pending

    pending = peek_pending(token)
    assert pending is not None
    return pending


def _executor(path: Path) -> Any:
    from dms_executor import Executor
    from dms_executor.demo_warehouse import SERVING_DIALECT

    return Executor(warehouse_path=path, dialect=SERVING_DIALECT)


def _patch_run(monkeypatch: pytest.MonkeyPatch, ran: list[str]) -> None:
    from dms_executor.sql_loop import run_readonly

    def wrapped(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return run_readonly(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", wrapped)


def _patch_save(monkeypatch: pytest.MonkeyPatch, saved: list[dict[str, Any]]) -> None:
    from dms_executor.verified_queries import maybe_save_checked_sql

    def wrapped(**kwargs: Any) -> Any:
        saved.append(kwargs)
        return maybe_save_checked_sql(**kwargs)

    monkeypatch.setattr("dms_executor.maybe_save_checked_sql", wrapped)
