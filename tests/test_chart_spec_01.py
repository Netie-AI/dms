"""CHART-SPEC-01. Synthetic columns only. No held-out pack."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "packages/executor/dms_executor/chart_spec.py"


def _load():
    spec = importlib.util.spec_from_file_location("chart_spec_unit", _PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("chart_spec module missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cs():
    return _load()


def _rows():
    return [
        {"country": "SENTINEL_ALPHA", "day": "2026-01-01", "spend": Decimal("8675309")},
        {"country": "SENTINEL_BETA", "day": "2026-01-02", "spend": Decimal("42")},
    ]


def test_only_digit_one_enables(cs, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CHART_SPEC", raising=False)
    assert cs.chart_spec_enabled() is False
    for raw in ("0", "true", "yes", "on", "2", ""):
        monkeypatch.setenv("DMS_CHART_SPEC", raw)
        assert cs.chart_spec_enabled() is False
    monkeypatch.setenv("DMS_CHART_SPEC", "1")
    assert cs.chart_spec_enabled() is True


def test_prompt_has_names_types_and_count_but_no_cell_values(cs) -> None:
    profile = cs.column_profile(_rows())
    assert profile == {
        "columns": [
            {"name": "country", "type": "text"},
            {"name": "day", "type": "date"},
            {"name": "spend", "type": "number"},
        ],
        "row_count": 2,
    }
    body = cs.request_body(profile)
    blob = json.dumps(body)
    assert "SENTINEL_ALPHA" not in blob
    assert "SENTINEL_BETA" not in blob
    assert "2026-01-01" not in blob
    assert "2026-01-02" not in blob
    assert "8675309" not in blob
    assert "42" not in blob
    assert body["role"] == "summarize"
    assert body["schema"] == "ChartSpec"
    assert "model" not in body
    user = json.loads(body["messages"][1]["content"])
    assert user == {
        "columns": profile["columns"],
        "row_count": 2,
    }


def test_missing_column_and_invented_number_fall_back(cs) -> None:
    profile = cs.column_profile([{"country": "MY", "spend": 10}])
    assert cs.validate_spec({"kind": "pie", "x": "missing", "y": ["spend"]}, profile) is None
    assert cs.validate_spec({"kind": "bar", "x": "country", "y": ["nope"]}, profile) is None
    kept = cs.validate_spec(
        {"kind": "pie", "x": "country", "y": "spend", "value": 99999, "series": None},
        profile,
    )
    assert kept == {"kind": "pie", "x": "country", "y": ["spend"], "series": None}
    assert "value" not in kept


def test_shapes(cs) -> None:
    profile = cs.column_profile(
        [{"country": "MY", "region": "W", "day": "2026-01-01", "spend": 1, "qty": 2}]
    )
    assert cs.validate_spec({"kind": "scatter", "x": "spend", "y": ["spend"]}, profile) is None
    scatter = cs.validate_spec({"kind": "scatter", "x": "spend", "y": ["qty"]}, profile)
    assert scatter["kind"] == "scatter"
    assert cs.validate_spec({"kind": "stacked", "x": "country", "y": ["spend"]}, profile) is None
    stacked = cs.validate_spec(
        {"kind": "stacked", "x": "country", "y": ["spend"], "series": "region"},
        profile,
    )
    assert stacked["series"] == "region"
    multi = cs.validate_spec(
        {"kind": "multi_line", "x": "day", "y": ["spend", "qty"]},
        profile,
    )
    assert multi["y"] == ["spend", "qty"]
    dup = cs.validate_spec({"kind": "bar", "x": "country", "y": ["spend", "spend"]}, profile)
    assert dup is None
    catalog = cs.adjust_catalog(profile)
    assert catalog["kinds"] == ["bar", "line", "pie", "stacked", "multi_line", "scatter"]
    numbers = cs.column_profile([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    assert cs.adjust_catalog(numbers)["kinds"] == ["line", "scatter"]


def test_flag_off_does_not_build_a_prompt_or_change_the_envelope(
    cs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_CHART_SPEC", raising=False)

    def boom(_profile):
        raise AssertionError("prompt built")

    monkeypatch.setattr(cs, "request_body", boom)
    env = {
        "abstained": False,
        "rows": _rows(),
        "route": "generated",
        "chart": {"kind": "hbar", "x": "country", "y": "spend"},
    }
    assert cs.attach_chart_spec(env) is env
    assert "chart_spec" not in env
    assert "chart_adjust" not in env


def test_flag_on_one_call_and_invalid_column_keeps_today_chart(
    cs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CHART_SPEC", "1")
    seen: list[dict] = []

    def transport(body):
        seen.append(body)
        return {"kind": "pie", "x": "missing", "y": ["spend"], "value": 99999}

    token = cs.bind_chart_transport(transport)
    try:
        env = {
            "abstained": False,
            "rows": _rows(),
            "route": "generated",
            "chart": {"kind": "hbar", "x": "country", "y": "spend"},
        }
        out = cs.attach_chart_spec(env)
    finally:
        cs.reset_chart_transport(token)
    assert len(seen) == 1
    blob = json.dumps(seen[0])
    assert "SENTINEL_ALPHA" not in blob
    assert "8675309" not in blob
    assert "chart_spec" not in out
    assert out["chart"] == env["chart"]
    assert out["route"] == "generated"
    assert "pie" in out["chart_adjust"]["kinds"]


def test_failed_call_does_not_raise_and_still_offers_adjust(
    cs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CHART_SPEC", "1")

    def transport(_body):
        raise cs.ChartSpecError("ov_unreachable")

    token = cs.bind_chart_transport(transport)
    try:
        out = cs.attach_chart_spec(
            {"abstained": False, "rows": [{"country": "MY", "spend": 1}], "route": "generated"}
        )
    finally:
        cs.reset_chart_transport(token)
    assert "chart_spec" not in out
    assert "bar" in out["chart_adjust"]["kinds"]
    assert out["route"] == "generated"


def test_fenced_and_chat_envelope_parse(cs) -> None:
    profile = cs.column_profile([{"country": "MY", "spend": 3}])
    fenced = "```json\n" + json.dumps({"kind": "bar", "x": "country", "y": ["spend"]}) + "\n```"
    assert cs.validate_spec(fenced, profile)["kind"] == "bar"
    wrapped = {
        "choices": [
            {"message": {"content": json.dumps({"kind": "pie", "x": "country", "y": ["spend"]})}}
        ]
    }
    assert cs.validate_spec(wrapped, profile)["kind"] == "pie"
    assert cs.validate_spec("not-json", profile) is None


def test_flag_off_ask_route_and_chart_match_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cortex_contract")
    from cortex_client.models import AskResponse
    from dms_executor import map_ask_response_to_envelope
    from dms_executor.chart_spec import bind_chart_transport, reset_chart_transport

    payload = {
        "answer": "Top sales by category.",
        "audit_id": "aud_chart_spec",
        "route": "query_skill",
        "provenance": {"badge": "query_skill", "layer": "L2"},
        "sql_used": "SELECT category, qty FROM t",
        "rows": [
            {"category": "Electronics", "qty": 12},
            {"category": "Food", "qty": 5},
        ],
    }

    def ask():
        return map_ask_response_to_envelope(
            AskResponse.model_validate(payload),
            space_id="sp_x",
            session_id="ses_chart",
        )

    monkeypatch.delenv("DMS_CHART_SPEC", raising=False)

    def boom(_body):
        raise AssertionError("chart call with the flag off")

    token = bind_chart_transport(boom)
    try:
        off = ask()
    finally:
        reset_chart_transport(token)
    assert "chart_spec" not in off
    assert "chart_adjust" not in off

    seen: list[dict] = []

    def transport(body):
        seen.append(body)
        return {"kind": "pie", "x": "category", "y": ["qty"], "value": 99999}

    monkeypatch.setenv("DMS_CHART_SPEC", "1")
    token = bind_chart_transport(transport)
    try:
        on = ask()
    finally:
        reset_chart_transport(token)
    assert len(seen) == 1
    user = json.loads(seen[0]["messages"][1]["content"])
    assert user == {
        "columns": [
            {"name": "category", "type": "text"},
            {"name": "qty", "type": "number"},
        ],
        "row_count": 2,
    }
    blob = json.dumps(seen[0])
    assert "Electronics" not in blob
    assert "Food" not in blob
    assert "12" not in blob
    assert "99999" not in blob
    assert seen[0]["role"] == "summarize"
    assert "model" not in seen[0]
    for key in ("route", "chart", "badge", "abstained", "sql_used", "text", "rows"):
        assert on[key] == off[key]
    assert on["chart_spec"] == {"kind": "pie", "x": "category", "y": ["qty"], "series": None}
