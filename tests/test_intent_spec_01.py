"""INTENT-SPEC-01: a second route's spec, checked against the SQL. No word lists."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.intent_spec import (
    ROLE_INTENT_SPEC,
    AnswerSpec,
    SpecFilter,
    check_sql_against_spec,
    masked_samples,
    parse_spec_payload,
    spec_prompt,
)

_WRITER = "writer-route"
_SPEC = "spec-route"
_ASC3 = (
    "SELECT category, SUM(amount) AS total FROM facts "
    "GROUP BY category ORDER BY total ASC LIMIT 3"
)
_DESC3 = (
    "SELECT category, SUM(amount) AS total FROM facts "
    "GROUP BY category ORDER BY total DESC LIMIT 3"
)
_ASC_ONE = (
    "SELECT category, SUM(amount) AS total FROM facts "
    "GROUP BY category ORDER BY total ASC LIMIT 1 OFFSET 2"
)
_DESC_ONE = (
    "SELECT category, SUM(amount) AS total FROM facts "
    "GROUP BY category ORDER BY total DESC LIMIT 1 OFFSET 2"
)
_NARROW = "SELECT note FROM sheet WHERE note = 'Ali'"


def _writer(sql: str, model: str = _WRITER) -> dict[str, Any]:
    return {
        "query_sql": sql,
        "served_model": model,
        "served_provider": "writer-provider",
        "route_id": "writer-route-id",
        "key_id": "writer-key",
        "tokens": {"total": 4},
    }


def _cited(
    *,
    direction: str | None = None,
    direction_span: str | None = None,
    n: int | None = None,
    n_span: str | None = None,
    offset: int | None = None,
    offset_span: str | None = None,
    literal: str | None = None,
    literal_span: str | None = None,
    model: str = _SPEC,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if direction is not None and direction_span is not None:
        body["direction"] = {"value": direction, "spans": [direction_span]}
    if n is not None and n_span is not None:
        body["n"] = {"value": n, "spans": [n_span]}
    if offset is not None and offset_span is not None:
        body["offset"] = {"value": offset, "spans": [offset_span]}
    if literal is not None and literal_span is not None:
        body["filters"] = [
            {"description": "entity", "literal": literal, "spans": [literal_span]}
        ]
    return {
        "intent_spec": body,
        "served_model": model,
        "served_provider": "spec-provider",
        "route_id": "spec-route-id",
        "key_id": "spec-key",
        "tokens": {"total": 2},
    }


def _seed(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE facts (category VARCHAR, amount INTEGER)")
        con.executemany(
            "INSERT INTO facts VALUES (?, ?)",
            [("a", 1), ("b", 2), ("c", 3), ("d", 10), ("e", 20), ("f", 30)],
        )
        con.execute("CREATE TABLE sheet (note VARCHAR)")
        con.executemany(
            "INSERT INTO sheet VALUES (?)",
            [("Ali",), ("Bea",)],
        )
    finally:
        con.close()


class _Harness:
    def __init__(self, lake: Path) -> None:
        self.lake = lake
        self.submits: list[str] = []
        self.calls: list[str | None] = []
        self.prompts: list[str | None] = []
        self.spec: dict[str, Any] = {}
        self.writer_sql = ""
        self.retry_sql = ""

    def submit(self, sql: str) -> Any:
        self.submits.append(sql)
        con = duckdb.connect(str(self.lake), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_spec", output={"rows": rows})

    def compute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        role = ctx.get("dms_route_role")
        self.calls.append(role if isinstance(role, str) else None)
        prompt = ctx.get("dms_route_prompt")
        self.prompts.append(prompt if isinstance(prompt, str) else None)
        if role == ROLE_INTENT_SPEC:
            return self.spec
        if isinstance(prompt, str) and "feedback:" in prompt:
            return _writer(self.retry_sql)
        return _writer(self.writer_sql)

    def ask(self, question: str) -> dict[str, Any] | None:
        return maybe_generative_ask(
            question,
            warehouse=self.lake,
            grantable={"facts", "sheet"},
            compute=self.compute,
            submit=self.submit,
            ledger_append=lambda _payload: SimpleNamespace(entry_id="led_spec", hash="hash_spec"),
            ontology=None,
            bind_on_miss=False,
        )


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    lake = tmp_path / "spec.duckdb"
    _seed(lake)
    monkeypatch.setenv("DMS_INTENT_SPEC", "1")
    return _Harness(lake)


def _categories(env: dict[str, Any] | None) -> list[Any]:
    assert env is not None
    return [row["category"] for row in env["rows"]]


@pytest.mark.parametrize(
    ("question", "span", "bad_sql", "good_sql", "n", "offset", "want"),
    [
        (
            "lowest 3 categories by stock value",
            "lowest",
            _DESC3,
            _ASC3,
            3,
            None,
            ["a", "b", "c"],
        ),
        (
            "bottom 3 categories by stock value",
            "bottom",
            _DESC3,
            _ASC3,
            3,
            None,
            ["a", "b", "c"],
        ),
        (
            "the three lowest categories by stock value",
            "three",
            _DESC3,
            _ASC3,
            3,
            None,
            ["a", "b", "c"],
        ),
        (
            "third-lowest category by stock value",
            "third-lowest",
            _DESC_ONE,
            _ASC_ONE,
            1,
            2,
            ["c"],
        ),
    ],
)
def test_ascending_spec_never_serves_descending_sql(
    harness: _Harness,
    question: str,
    span: str,
    bad_sql: str,
    good_sql: str,
    n: int,
    offset: int | None,
    want: list[str],
) -> None:
    harness.spec = _cited(
        direction="asc",
        direction_span=span,
        n=n,
        n_span=span,
        offset=offset,
        offset_span=span if offset is not None else None,
    )
    harness.writer_sql = bad_sql
    harness.retry_sql = bad_sql
    env = harness.ask(question)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "intent_spec_mismatch" in env["text"]
    assert "ascending" in env["text"]
    assert harness.submits == []
    _ = good_sql, want


@pytest.mark.parametrize(
    ("question", "span", "sql", "n", "offset", "want"),
    [
        ("lowest 3 categories by stock value", "lowest", _ASC3, 3, None, ["a", "b", "c"]),
        ("bottom 3 categories by stock value", "bottom", _ASC3, 3, None, ["a", "b", "c"]),
        (
            "the three lowest categories by stock value",
            "three",
            _ASC3,
            3,
            None,
            ["a", "b", "c"],
        ),
        (
            "third-lowest category by stock value",
            "third-lowest",
            _ASC_ONE,
            1,
            2,
            ["c"],
        ),
    ],
)
def test_ascending_sql_serves_the_low_rows(
    harness: _Harness,
    question: str,
    span: str,
    sql: str,
    n: int,
    offset: int | None,
    want: list[str],
) -> None:
    harness.spec = _cited(
        direction="asc",
        direction_span=span,
        n=n,
        n_span=span,
        offset=offset,
        offset_span=span if offset is not None else None,
    )
    harness.writer_sql = sql
    harness.retry_sql = sql
    env = harness.ask(question)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert _categories(env) == want
    for name in want:
        assert name in env["text"]
    assert harness.submits == [sql]


def test_retry_then_serves_the_ascending_rows(harness: _Harness) -> None:
    question = "lowest 3 categories by stock value"
    harness.spec = _cited(direction="asc", direction_span="lowest", n=3, n_span="3")
    harness.writer_sql = _DESC3
    harness.retry_sql = _ASC3
    env = harness.ask(question)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert _categories(env) == ["a", "b", "c"]
    assert "a" in env["text"] and "f" not in env["text"]
    assert harness.submits == [_ASC3]


def test_top3_descending_spec_serves_the_high_rows(harness: _Harness) -> None:
    question = "top 3 categories by stock value"
    harness.spec = _cited(direction="desc", direction_span="top", n=3, n_span="3")
    harness.writer_sql = _DESC3
    harness.retry_sql = _DESC3
    env = harness.ask(question)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert _categories(env) == ["f", "e", "d"]
    assert "f" in env["text"]
    assert harness.submits == [_DESC3]


def test_span_outside_the_question_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    parsed = parse_spec_payload(
        _cited(direction="asc", direction_span="not-in-the-question"),
        "lowest 3 categories by stock value",
        writer_payload=_writer("SELECT 1"),
    )
    assert parsed.unverified is None
    assert parsed.spec.direction is None
    assert parsed.dropped == ({"field": "direction", "reason": "span_not_in_question"},)
    assert "dropped_field" in caplog.text
    assert "span_not_in_question" in caplog.text


def test_same_model_abstains(harness: _Harness) -> None:
    harness.spec = _cited(
        direction="asc", direction_span="lowest", n=3, n_span="3", model=_WRITER
    )
    harness.writer_sql = _ASC3
    env = harness.ask("lowest 3 categories by stock value")
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "intent_spec_unverified:same_model" in env["text"]
    assert harness.submits == []


def test_missing_spec_abstains(harness: _Harness) -> None:
    harness.spec = {"served_model": _SPEC}
    harness.writer_sql = _ASC3
    env = harness.ask("lowest 3 categories by stock value")
    assert env is not None
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "intent_spec_unverified:spec_missing" in env["text"]
    assert harness.submits == []


def test_unreported_writer_model_abstains(harness: _Harness) -> None:
    harness.spec = _cited(direction="asc", direction_span="lowest", n=3, n_span="3")
    harness.writer_sql = _ASC3
    harness.compute = lambda ctx: (  # type: ignore[method-assign]
        harness.spec
        if ctx.get("dms_route_role") == ROLE_INTENT_SPEC
        else {"query_sql": _ASC3}
    )
    env = harness.ask("lowest 3 categories by stock value")
    assert env is not None
    assert env["abstained"] is True
    assert "intent_spec_unverified:writer_model_unreported" in env["text"]
    assert harness.submits == []


def test_flag_off_does_not_stamp_a_spec_attempt(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_INTENT_SPEC", raising=False)
    harness.writer_sql = _DESC3
    env = harness.ask("lowest 3 categories by stock value")
    assert env is not None
    assert "intent_spec_attempt" not in env
    assert harness.calls == [None]
    assert harness.submits == [_DESC3]


def _mask_clock(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: ("<as_of>" if key == "as_of" else _mask_clock(val))
            for key, val in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_clock(val) for val in obj]
    return obj


_WHERE_FALSE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "ask_guide"
    / "flag_off_where_false_ce08153.json"
)
_FALSE_SQL = "SELECT category, amount FROM facts WHERE 1 = 2"


def test_flag_off_where_false_matches_main_empty_rows(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Flag off executes ``WHERE 1 = 2`` and withholds the empty rows, like main.

    The stub runs the SQL. Main's envelope is the ce08153 capture. No
    pipeline-failure ticket is written.
    """
    monkeypatch.delenv("DMS_INTENT_SPEC", raising=False)
    harness.writer_sql = _FALSE_SQL
    with caplog.at_level(logging.WARNING, logger="dms_executor.intent_spec"):
        env = harness.ask("show every row")
    assert env is not None
    assert_envelope_valid(env)
    assert harness.submits == [_FALSE_SQL]
    main = json.loads(_WHERE_FALSE.read_text(encoding="utf-8"))
    assert _mask_clock(env) == _mask_clock(main)
    assert "pipeline_failure" not in caplog.text


def test_flag_on_where_false_abstains_as_contradiction(harness: _Harness) -> None:
    """Flag on still refuses always-false SQL. The spec cites the question."""
    harness.spec = _cited(direction="none", direction_span="show every row")
    harness.writer_sql = _FALSE_SQL
    harness.retry_sql = _FALSE_SQL
    env = harness.ask("show every row")
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "contradiction" in env["text"]
    assert harness.submits == []


def test_always_true_comparison_still_serves(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_INTENT_SPEC", raising=False)
    harness.writer_sql = "SELECT category, amount FROM facts WHERE 1 <> 2"
    env = harness.ask("show every row")
    assert env is not None
    assert env["abstained"] is False
    assert _categories(env) == ["a", "b", "c", "d", "e", "f"]
    assert len(harness.submits) == 1


def test_narrowed_entity_span_is_not_served(harness: _Harness) -> None:
    """`show ali bin` cites `ali bin`. SQL `= 'Ali'` covers only part of that span."""
    question = "show ali bin"
    harness.spec = _cited(literal="Ali", literal_span="ali bin")
    harness.writer_sql = _NARROW
    harness.retry_sql = _NARROW
    env = harness.ask(question)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "not fully covered" in env["text"]
    assert "ali bin" in env["text"]
    assert harness.submits == []
    assert all(prompt is None or "samples:" not in prompt for prompt in harness.prompts)


def test_full_entity_span_is_served(harness: _Harness) -> None:
    """`show Ali` with SQL `= 'Ali'` covers the cited span."""
    question = "show Ali"
    harness.spec = _cited(literal="Ali", literal_span="Ali")
    harness.writer_sql = _NARROW
    harness.retry_sql = _NARROW
    env = harness.ask(question)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["rows"] == [{"note": "Ali"}]
    assert "Ali" in env["text"]
    assert "Bea" not in env["text"]
    assert harness.submits == [_NARROW]


def test_partial_literal_does_not_cover_a_span() -> None:
    sql = "SELECT note FROM sheet WHERE note = 'aa'"
    reason = check_sql_against_spec(
        sql, AnswerSpec(filters=(SpecFilter("entity", "aa", ("aa bb",)),))
    )
    assert reason is not None
    assert "not fully covered" in reason
    assert check_sql_against_spec(
        "SELECT note FROM sheet WHERE note = 'aa bb'",
        AnswerSpec(filters=(SpecFilter("entity", "aa bb", ("aa bb",)),)),
    ) is None
    stitched = (
        "SELECT note FROM sheet WHERE note = 'aa' AND other = 'bb'"
    )
    stitched_reason = check_sql_against_spec(
        stitched, AnswerSpec(filters=(SpecFilter("entity", "aa", ("aa bb",)),))
    )
    assert stitched_reason is not None
    assert "not fully covered" in stitched_reason


def _tickets(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for rec in caplog.records:
        message = rec.getMessage()
        if not message.startswith("pipeline_failure "):
            continue
        body = message.split(" ", 1)[1]
        if not body.startswith("{"):
            continue
        found.append(json.loads(body))
    return found


def test_flag_on_mismatch_writes_pipeline_failure_ticket(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """A mismatch ticket names the question, the rejected SQL, and the retry count.

    Literals go through the masker. A bearer value does not survive.
    """
    email = "ada@example.com"
    bearer = "sk-abcdefghij1234"
    question = f"lowest 3 {email}"
    sql = (
        "SELECT category, SUM(amount) AS total FROM facts "
        f"WHERE category <> '{bearer}' "
        "GROUP BY category ORDER BY total DESC LIMIT 3"
    )
    harness.spec = _cited(direction="asc", direction_span="lowest", n=3, n_span="3")
    harness.writer_sql = sql
    harness.retry_sql = sql
    with caplog.at_level(logging.WARNING, logger="dms_executor.intent_spec"):
        env = harness.ask(question)
    assert env is not None
    assert env["abstained"] is True
    assert "intent_spec_mismatch" in env["text"]
    tickets = _tickets(caplog)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert set(ticket) >= {"question", "rejected_sql", "retry_count"}
    assert ticket["retry_count"] == 2
    blob = json.dumps(ticket)
    assert email not in blob
    assert bearer not in blob
    assert "DMSMASK_email" in ticket["question"]
    assert "[redacted]" in ticket["rejected_sql"]


def test_flag_on_unverified_writes_pipeline_failure_ticket(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    harness.spec = {}
    harness.writer_sql = _ASC3
    with caplog.at_level(logging.WARNING, logger="dms_executor.intent_spec"):
        env = harness.ask("lowest 3 categories by stock value")
    assert env is not None
    assert "intent_spec_unverified" in env["text"]
    tickets = _tickets(caplog)
    assert len(tickets) == 1
    assert tickets[0]["retry_count"] == 0
    assert tickets[0]["question"] == "lowest 3 categories by stock value"
    assert "ORDER BY total ASC" in tickets[0]["rejected_sql"]


def test_ticket_writing_does_not_change_the_abstain_body(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller envelope is the same when the ticket writer raises."""
    import dms_executor.intent_spec as spec

    harness.spec = _cited(direction="asc", direction_span="lowest", n=3, n_span="3")
    harness.writer_sql = _DESC3
    harness.retry_sql = _DESC3
    question = "lowest 3 categories by stock value"
    first = harness.ask(question)

    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("ticket writer down")

    monkeypatch.setattr(spec, "log_pipeline_failure_ticket", _boom)
    second = harness.ask(question)
    assert first is not None and second is not None
    assert _mask_clock(first) == _mask_clock(second)


def test_direction_mismatch_names_the_order() -> None:
    reason = check_sql_against_spec(_DESC3, AnswerSpec(direction="asc", n=3))
    assert reason == "spec says ascending, SQL orders DESC"


def test_text_column_samples_are_omitted() -> None:
    rows = [{"note": "Ali", "amount": 1}]
    assert masked_samples(rows, {"note"}) == [{"amount": 1}]
    prompt = spec_prompt("show ali bin", samples=rows, text_columns={"note"})
    assert "samples:" in prompt
    assert "Ali" not in prompt.split("samples:", 1)[1]
    bare = spec_prompt("show ali bin")
    assert "samples:" not in bare


def test_rank_window_limit_agrees_with_the_spec() -> None:
    sql = (
        "SELECT category, amount FROM ("
        "SELECT category, amount, ROW_NUMBER() OVER (ORDER BY amount ASC) AS rn "
        "FROM facts) AS ranked WHERE rn <= 3"
    )
    assert check_sql_against_spec(sql, AnswerSpec(direction="asc", n=3)) is None
    outer = sql + " ORDER BY amount DESC"
    reason = check_sql_against_spec(outer, AnswerSpec(direction="asc", n=3))
    assert reason is not None
    assert "DESC" in reason
