"""UX-CHART-01. Flag off keeps the envelope object. Flag on replaces a row listing."""

from __future__ import annotations

import pytest
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.ux_chart import attach_ux_chart, ux_chart_enabled


def _listing(rows: list[dict]) -> str:
    lines = ["  - " + ", ".join(f"{k}={v}" for k, v in row.items()) for row in rows]
    return f"Found {len(rows)} row(s).\n" + "\n".join(lines)


def _build(text: str, rows: list[dict], **kwargs: object):
    return build_answer_envelope(
        answer_id="ans_ux",
        text=text,
        badge="L2_VALIDATED",
        rows=rows,
        sql_used="SELECT category, stock_value_myr FROM inventory",
        assumptions=["ux-chart"],
        ask_mode="demo",
        **kwargs,
    )


_TWO = [
    {"category": "ELECTRONICS", "stock_value_myr": 69729521.94},
    {"category": "PACKAGING", "stock_value_myr": 15015.0},
]


def test_flag_off_is_the_same_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_UX_CHART", raising=False)
    assert ux_chart_enabled() is False
    env = {"text": _listing(_TWO), "rows": _TWO, "values": []}
    assert attach_ux_chart(env) is env
    assert "ux_chart" not in env
    assert env["text"] == _listing(_TWO)


def test_flag_off_envelope_text_is_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_UX_CHART", raising=False)
    text = _listing(_TWO)
    env = _build(text, _TWO)
    assert env["text"] == text
    assert "ux_chart" not in env
    assert env["rows"] == _TWO
    assert_envelope_valid(env)


def test_flag_on_replaces_the_listing_and_keeps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_UX_CHART", "1")
    env = _build(_listing(_TWO), _TWO)
    assert env["ux_chart"] is True
    assert "category=" not in env["text"]
    assert "stock_value_myr=" not in env["text"]
    assert "ELECTRONICS is highest at RM 69,729,521.94" in env["text"]
    assert "across 2 rows." in env["text"]
    assert "total RM " in env["text"]
    assert env["rows"] == _TWO
    assert len(env["rows"]) == 2
    assert_envelope_valid(env)


def test_flag_on_does_not_cap_the_sentence_at_twelve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_UX_CHART", "1")
    rows = [{"category": f"C{i}", "stock_value_myr": float(i + 1)} for i in range(30)]
    env = _build(_listing(rows), rows)
    assert len(env["rows"]) == 30
    assert env["rows"] == rows
    assert "across 30 rows." in env["text"]
    assert "category=" not in env["text"]
    assert_envelope_valid(env)


def test_flag_on_leaves_a_real_sentence_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_UX_CHART", "1")
    text = "Home revenue is 1,199,018.49 MYR."
    rows = [{"category": "Home", "sales_value_myr": 1199018.49}]
    env = _build(text, rows)
    assert env["text"] == text
    assert env["ux_chart"] is True
    assert env["rows"] == rows
    assert_envelope_valid(env)


def test_flag_on_abstain_stays_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_UX_CHART", "1")
    env = build_answer_envelope(
        answer_id="ans_abs",
        text="No.",
        badge="ABSTAIN",
        abstained=True,
        ask_mode="demo",
    )
    assert env["ux_chart"] is True
    assert env["text"] == "No."
    assert env["rows"] == []
    assert env["values"] == []
    assert_envelope_valid(env)


def test_other_digits_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_UX_CHART", "true")
    env = {"text": _listing(_TWO), "rows": list(_TWO)}
    assert attach_ux_chart(env) is env
