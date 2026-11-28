"""BIRD-SERVING-DEADLINE-01. ``DMS_DEADLINE_ADAPTIVE`` defaults off.

Off, an expired ask clock still abstains ``serving_deadline_reserve`` and the
prompt bytes, envelope, and route stay as they are. On, a generator that
keeps producing a SQL candidate is served, HTTP 429 backs off without the
transport text, and the envelope carries ``stage_ms``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import httpx
import pytest
from cortex_client.ask_clock import (
    attach_timings,
    deadline_adaptive_enabled,
    ensure_clock,
    release_clock,
    span,
)
from cortex_client.compute import (
    INSIGHTS_FAIL_AI_ERROR,
    _insights_generate_post,
    begin_answer_model_calls,
    recorded_model_calls,
)
from dms_core.ask_stream import (
    bind_ask,
    clear_asks,
    finish_ask,
    stream_enabled,
    wait_events,
)
from dms_executor.demo_warehouse import SERVING_DIALECT, connect_serving, ensure_demo_warehouse
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.lake_registry import (
    AskControl,
    ServingDeadlineReserve,
    ServingWaitCancelled,
    bind_ask_control,
    bind_ask_deadline,
    raise_if_ask_stopped,
    reset_ask_control,
    reset_ask_deadline,
)
from dms_executor.ontology import demo_ontology

_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_COLD = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_SERVED = ("badge", "abstained", "sql_used", "rows", "route", "text", "values")
_LEAK = "TRANSPORT_LEAK_TEXT"
_ASK_ID = "bird-deadline-ask-1"


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_DEADLINE_ADAPTIVE", raising=False)
    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    assert deadline_adaptive_enabled() is False
    assert stream_enabled() is False
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "0")
    assert deadline_adaptive_enabled() is False
    assert stream_enabled() is False


def test_flag_off_expired_clock_is_still_the_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_DEADLINE_ADAPTIVE", raising=False)
    lake = _tiny_lake(tmp_path / "off.duckdb")
    tokens = bind_ask_deadline(time.monotonic() - 1.0)
    try:
        with pytest.raises(ServingDeadlineReserve) as raised:
            connect_serving(lake)
        assert raised.value.code == "serving_deadline_reserve"
    finally:
        reset_ask_deadline(tokens)


def test_adaptive_read_continues_after_the_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "1")
    lake = _tiny_lake(tmp_path / "on.duckdb")
    tokens = bind_ask_deadline(time.monotonic() - 1.0)
    try:
        cur = connect_serving(lake)
        try:
            row = cur.execute("SELECT id FROM t").fetchone()
            assert row == (1,)
        finally:
            cur.close()
    finally:
        reset_ask_deadline(tokens)


def test_client_cancel_still_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "1")
    idle = AskControl()
    idle.deadline = time.monotonic() - 5.0
    assert idle._interrupt_due() is False
    ctrl = AskControl()
    ctrl.deadline = time.monotonic() - 5.0
    ctrl.disconnected.set()
    token = bind_ask_control(ctrl)
    try:
        assert ctrl._interrupt_due() is True
        with pytest.raises(ServingWaitCancelled):
            raise_if_ask_stopped()
    finally:
        reset_ask_control(token)


def test_stage_ms_sums_on_a_fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "1")
    ticks = {"t": 0.0}
    monkeypatch.setattr("cortex_client.ask_clock.time.perf_counter", lambda: ticks["t"])
    token = ensure_clock()
    try:
        with span("insights_generate"):
            ticks["t"] = 0.4
        with span("validate_explain"):
            ticks["t"] = 0.7
        env = attach_timings({"route": "generated", "sql_used": "SELECT 1"})
    finally:
        release_clock(token)
    assert env is not None
    stage = env["stage_ms"]
    assert isinstance(stage, dict)
    parts = sum(value for name, value in stage.items() if name != "total")
    assert abs(parts - stage["total"]) <= 1.0
    assert stage["insights_generate"] == pytest.approx(400.0, abs=1.0)
    assert stage["validate_explain"] == pytest.approx(300.0, abs=1.0)
    assert env["route"] == "generated"
    assert env["sql_used"] == "SELECT 1"
    assert "rate_limit_retries" not in env


def test_flag_off_envelope_has_no_stage_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_DEADLINE_ADAPTIVE", raising=False)
    token = ensure_clock()
    try:
        with span("insights_generate"):
            pass
        env = attach_timings({"route": "generated"})
    finally:
        release_clock(token)
    assert env == {"route": "generated"}


def test_flag_off_prompt_and_served_route_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    prompts: dict[str, bytes] = {}

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        prompts[os_flag()] = _prompt_bytes(ctx)
        return _names(query_sql=_COLD)

    monkeypatch.delenv("DMS_DEADLINE_ADAPTIVE", raising=False)
    unset = _ask(tmp_path / "unset", "Which locations are cold storage?", compute)
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "0")
    zero = _ask(tmp_path / "zero", "Which locations are cold storage?", compute)
    assert unset is not None and zero is not None
    assert prompts[""] == prompts["0"]
    assert prompts[""]
    assert _served(unset) == _served(zero)
    assert "stage_ms" not in unset
    assert "stage_ms" not in zero
    assert unset["route"] == zero["route"]


def test_slow_generator_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A generator that sleeps past the clock and returns SQL is served."""
    _loop(monkeypatch)
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "1")
    monkeypatch.delenv("DMS_ASK_STREAM", raising=False)
    assert stream_enabled() is True
    calls = {"n": 0}

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        del ctx
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.15)
        return _names(query_sql=_COLD)

    clear_asks()
    stream_token = bind_ask(_ASK_ID)
    tokens = bind_ask_deadline(time.monotonic() - 1.0)
    try:
        env = _ask(tmp_path / "slow", "Which locations are cold storage?", compute)
        events, _done = wait_events(_ASK_ID, 0, 0.05)
    finally:
        reset_ask_deadline(tokens)
        finish_ask(stream_token)
        clear_asks()
    assert calls["n"] >= 1
    assert env is not None
    assert env.get("abstained") is False
    assert env.get("sql_used") == _COLD
    assert env.get("route")
    assert "serving_deadline_reserve" not in str(env.get("abstain_reason") or "")
    stage = env.get("stage_ms")
    assert isinstance(stage, dict)
    assert stage["total"] >= 0
    assert any(event.get("stage") == "generate" for event in events)
    assert any(event.get("stage") == "validate" for event in events)


def test_flag_off_slow_generator_hits_the_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    monkeypatch.delenv("DMS_DEADLINE_ADAPTIVE", raising=False)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        del ctx
        return _names(query_sql=_COLD)

    tokens = bind_ask_deadline(time.monotonic() - 1.0)
    try:
        with pytest.raises(ServingDeadlineReserve):
            _ask(tmp_path / "late", "Which locations are cold storage?", compute)
    finally:
        reset_ask_deadline(tokens)


def test_rate_limit_twice_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ADAPTIVE", "1")
    slept: list[float] = []
    monkeypatch.setattr(
        "cortex_client.compute.time.sleep", lambda delay: slept.append(float(delay))
    )
    http = _RateLimitHttp()
    begin_answer_model_calls()
    token = ensure_clock()
    try:
        payload = _insights_generate_post(
            http,  # type: ignore[arg-type]
            "http://example.test",
            {"intent": "rows", "question": "rows"},
            None,
        )
        env = attach_timings({"route": "generated"})
    finally:
        release_clock(token)
    assert http.codes_seen == [429, 429, 200]
    assert http.bodies[0] == http.bodies[1] == http.bodies[2]
    assert isinstance(payload, dict)
    assert payload.get("query_sql") == "SELECT 1"
    assert payload.get("rate_limit_retries") == 2
    assert recorded_model_calls() == 1
    assert len(slept) == 2
    assert all(delay > 0 for delay in slept)
    blob = json.dumps(payload, default=str)
    assert _LEAK not in blob
    assert env is not None
    assert env.get("rate_limit_retries") == 2


def test_flag_off_does_not_retry_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_DEADLINE_ADAPTIVE", raising=False)
    slept: list[float] = []
    monkeypatch.setattr(
        "cortex_client.compute.time.sleep", lambda delay: slept.append(float(delay))
    )
    http = _RateLimitHttp()
    begin_answer_model_calls()
    payload = _insights_generate_post(
        http,  # type: ignore[arg-type]
        "http://example.test",
        {"intent": "rows", "question": "rows"},
        None,
    )
    assert http.codes_seen == [429]
    assert slept == []
    assert isinstance(payload, dict)
    assert payload.get("insights_fail") == INSIGHTS_FAIL_AI_ERROR
    assert payload.get("ai_call_status") == 429
    assert "rate_limit_retries" not in payload
    assert _LEAK not in json.dumps(payload, default=str)
    assert recorded_model_calls() == 1


class _RateLimitHttp:
    def __init__(self) -> None:
        self.codes_seen: list[int] = []
        self.bodies: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        del url
        body = kwargs.get("json")
        self.bodies.append(dict(body) if isinstance(body, dict) else {})
        code = 200 if len(self.codes_seen) >= 2 else 429
        self.codes_seen.append(code)
        if code == 429:
            return httpx.Response(429, json={"error": _LEAK, "message": _LEAK})
        return httpx.Response(200, json={"status": "OK", "query_sql": "SELECT 1", "values": []})


def _tiny_lake(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t (id INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
    finally:
        con.close()
    return path


def _loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")


def os_flag() -> str:
    return os.environ.get("DMS_DEADLINE_ADAPTIVE", "")


def _prompt_bytes(ctx: dict[str, Any]) -> bytes:
    parts: list[str] = []
    for key in sorted(ctx):
        value = ctx[key]
        if isinstance(value, str):
            parts.append(f"{key}={value}")
    return "\n".join(parts).encode("utf-8")


def _names(**extra: Any) -> dict[str, Any]:
    base = {"served_model": "fake", "served_provider": "groq", "phase": "generate"}
    base.update(extra)
    return base


def _served(env: dict[str, Any]) -> dict[str, Any]:
    return {key: env.get(key) for key in _SERVED}


def _ask(path: Path, question: str, compute: Any) -> dict[str, Any] | None:
    begin_answer_model_calls()
    db = path / "bird.duckdb"
    ensure_demo_warehouse(db)

    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(col[0]) for col in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_bird", output={"rows": rows})

    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS),
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="h"),
        ontology=load_verified_ontology(db, demo_ontology(db)),
        dialect=SERVING_DIALECT,
    )
