"""Pipeline-failure tickets from the extract loop.

One group per reason and masked question. The ask envelope does not change.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import dms_executor.pipeline_failure as tickets
import pytest
from dms_executor.pipeline_failure import log_pipeline_failure_ticket
from test_c_loop_b import (
    _COLD_SQL,
    _EMPTY_SQL,
    _ask,
    _assert_abstain,
    _loop,
    _names,
)

_LITERAL = "Bearer seeded-literal-9f3a"
_CLOCK = {
    "as_of",
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
}


@pytest.fixture(autouse=True)
def _clear_groups() -> None:
    tickets._reset_pipeline_failures()


def _records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in caplog.records:
        text = rec.getMessage()
        if not text.startswith("pipeline_failure "):
            continue
        got = json.loads(text[len("pipeline_failure ") :])
        assert isinstance(got, dict)
        out.append(got)
    return out


def _groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = str(row["group"])
        prev = best.get(group)
        if prev is None or int(row["count"]) >= int(prev["count"]):
            best[group] = row
    return best


def _freeze(env: dict[str, Any]) -> dict[str, Any]:
    def norm(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for key, val in obj.items():
                name = str(key)
                if name in _CLOCK and isinstance(val, str):
                    out[name] = "CLOCK"
                else:
                    out[name] = norm(val)
            return out
        if isinstance(obj, list):
            return [norm(item) for item in obj]
        if isinstance(obj, float):
            return round(obj, 6)
        return obj

    frozen = json.loads(json.dumps(norm(env), sort_keys=True, default=str))
    assert isinstance(frozen, dict)
    return frozen


def _watch(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


def test_flag_on_abstain_kinds_each_write_one_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    cases = (
        ("empty_result_unverified:value_exists_pending", _EMPTY_SQL, "expiring chemicals"),
        ("loop_exhausted:", "SELECT nope FROM inventory", "cold storage locations"),
        ("checker:hostile_sql:", "SELECT * FROM read_csv('notes.csv')", "sheet rows"),
    )
    for prefix, sql, question in cases:
        caplog.clear()
        tickets._reset_pipeline_failures()

        def compute(_ctx: dict[str, Any], sql: str = sql) -> dict[str, Any]:
            return _names(query_sql=sql)

        env = _assert_abstain(_ask(tmp_path, question, compute))
        rows = _records(caplog)
        grouped = _groups(rows)
        assert len(grouped) == 1, question
        ticket = next(iter(grouped.values()))
        assert str(ticket["reason"]).startswith(prefix), ticket
        assert ticket["stage"] == "extract_loop"
        assert question in str(ticket.get("question"))
        assert isinstance(ticket.get("sql"), str) and ticket["sql"]
        assert ticket["retries"] == (1 if prefix == "loop_exhausted:" else 0)
        assert "pipeline_failure" not in json.dumps(env)


def test_flag_off_writes_no_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    _watch(caplog)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql="SELECT nope FROM inventory")

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert _records(caplog) == []
    assert "pipeline_failure" not in json.dumps(env)


def test_served_answer_keeps_attribution_and_writes_no_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_COLD_SQL)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["served_attribution"] == "reported"
    assert _records(caplog) == []


def test_identical_abstains_are_one_group_and_envelopes_match(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    question = "Which chemicals are expiring this month?"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_EMPTY_SQL)

    bodies = []
    for _ in range(3):
        bodies.append(_freeze(_assert_abstain(_ask(tmp_path, question, compute))))
    assert bodies[0] == bodies[1] == bodies[2]
    grouped = _groups(_records(caplog))
    assert len(grouped) == 1
    ticket = next(iter(grouped.values()))
    assert ticket["count"] == 3
    assert ticket["reason"] == "empty_result_unverified:value_exists_pending"


def test_writer_failure_leaves_the_envelope_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    question = "Which chemicals are expiring this month?"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_EMPTY_SQL)

    before = _freeze(_assert_abstain(_ask(tmp_path, question, compute)))

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("ticket down")

    monkeypatch.setattr(tickets, "log_pipeline_failure_ticket", _boom)
    caplog.clear()
    after = _freeze(_assert_abstain(_ask(tmp_path, question, compute)))
    assert after == before
    assert any(
        rec.getMessage() == "pipeline_failure ticket was not written"
        for rec in caplog.records
    )


def test_seeded_literal_is_redacted_and_mask_failure_omits_text(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(caplog)
    question = f"Which locations are cold storage? {_LITERAL}"
    sql = f"SELECT sku FROM inventory -- {_LITERAL}"
    log_pipeline_failure_ticket("checker:hostile_sql:x", question, sql, 0, "extract_loop")
    blob = caplog.text
    assert _LITERAL not in blob
    assert "seeded-literal-9f3a" not in blob
    assert "cold storage" in blob
    caplog.clear()
    tickets._reset_pipeline_failures()

    def _fail(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mask down")

    monkeypatch.setattr("dms_core.pii.fail_closed_mask_payload", _fail)
    log_pipeline_failure_ticket("checker:hostile_sql:x", question, sql, 0, "extract_loop")
    blob = caplog.text
    assert _LITERAL not in blob
    assert "seeded-literal-9f3a" not in blob
    row = _records(caplog)[-1]
    assert "question" not in row
    assert "sql" not in row
    assert row["count"] == 1


def test_reason_and_question_split_groups(caplog: pytest.LogCaptureFixture) -> None:
    _watch(caplog)
    log_pipeline_failure_ticket("empty_result_unverified", "question one", "SELECT 1", 0, "s")
    log_pipeline_failure_ticket("loop_exhausted:checker", "question one", "SELECT 1", 1, "s")
    log_pipeline_failure_ticket("empty_result_unverified", "question two", "SELECT 2", 0, "s")
    grouped = _groups(_records(caplog))
    assert len(grouped) == 3
    assert {row["count"] for row in grouped.values()} == {1}


def test_cap_evicts_and_stays_bounded(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(caplog)
    monkeypatch.setattr(tickets, "PIPELINE_FAILURE_CAP", 2)
    for index in range(3):
        log_pipeline_failure_ticket("same", f"question {index}", "SELECT 1", 0, "s")
    assert len(tickets._GROUPS) <= 2
    caplog.clear()
    tickets_before = len(tickets._GROUPS)
    log_pipeline_failure_ticket("same", "question 0", "SELECT 1", 0, "s")
    assert len(tickets._GROUPS) <= 2
    assert tickets_before <= 2
    revived = next(iter(_groups(_records(caplog)).values()))
    assert revived["count"] == 1


def test_threads_share_one_group_count() -> None:
    import threading

    question = "threaded question"
    errors: list[BaseException] = []

    def _one() -> None:
        try:
            log_pipeline_failure_ticket("threaded", question, "SELECT 1", 0, "s")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_one) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(tickets._GROUPS) == 1
    count, _seen = next(iter(tickets._GROUPS.values()))
    assert count == 8
