"""LATENCY-01: stage timings, and DMS_ASK_LEAN cuts default off.

A fake Cortex with a controllable delay covers cancel, deferred insights,
the token cap, monotonic timings, and flag-off prompt bytes. Schemas here
are synthetic. No held-out pack.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.ask_clock import (
    ensure_clock,
    model_slot,
    note_deadline,
    release_clock,
    span,
)
from cortex_client.compute import begin_answer_model_calls, compute_insights
from dms_executor.ask_lean import block_tokens
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology

ROOT = Path(__file__).resolve().parents[1]
_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_COLD = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_SERVED = ("badge", "abstained", "sql_used", "rows", "route", "text", "values")


class _HoldHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.endswith("/cancel"):
            self.server.cancel_count += 1  # type: ignore[attr-defined]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.server.open_count += 1  # type: ignore[attr-defined]
        try:
            self.connection.settimeout(0.05)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    chunk = self.connection.recv(1)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
        finally:
            self.server.open_count -= 1  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        del fmt, args


class _HoldServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _HoldHandler)
        self.cancel_count = 0
        self.open_count = 0
        self.daemon_threads = True


def _serve() -> tuple[_HoldServer, threading.Thread]:
    server = _HoldServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _wait_closed(server: _HoldServer) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if server.open_count == 0:
            return
        time.sleep(0.02)
    raise AssertionError(f"request still open: {server.open_count}")


def _names(**extra: Any) -> dict[str, Any]:
    base = {"served_model": "fake", "served_provider": "groq", "phase": "generate"}
    base.update(extra)
    return base


def _ask(tmp_path: Path, question: str, compute: Any) -> dict[str, Any] | None:
    begin_answer_model_calls()
    db = tmp_path / "latency.duckdb"
    ensure_demo_warehouse(db)

    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_lat", output={"rows": rows})

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


def _served(env: dict[str, Any]) -> dict[str, Any]:
    return {key: env.get(key) for key in _SERVED}


def _loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")


def test_cancel_closes_the_request_before_the_ask_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out generate is closed and Cortex is told to cancel."""
    server, thread = _serve()
    host, port = server.server_address
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "0.3")
    monkeypatch.setenv("DMS_ASK_LEAN_CANCEL", "1")
    token = ensure_clock()
    try:
        payload = compute_insights(
            f"http://{host}:{port}",
            question="how many rows",
            api_key="latency-probe-key",
            session_id="s",
            space_id="sp",
        )
    finally:
        release_clock(token)
        _wait_closed(server)
        server.shutdown()
        thread.join(1.0)
    assert isinstance(payload, dict)
    assert str(payload.get("insights_fail") or "").startswith("insights_timeout")
    assert server.cancel_count >= 1
    assert server.open_count == 0


def test_ask_deadline_cancels_a_call_the_leg_timeout_would_keep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cancel cut stops a call at the ask deadline, not at the longer leg timeout."""
    server, thread = _serve()
    host, port = server.server_address
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "30")
    monkeypatch.setenv("DMS_ASK_LEAN_CANCEL", "1")
    token = ensure_clock()
    note_deadline(time.monotonic() + 0.3)
    started = time.perf_counter()
    try:
        payload = compute_insights(
            f"http://{host}:{port}",
            question="how many rows",
            api_key="latency-probe-key",
            session_id="s",
            space_id="sp",
        )
    finally:
        elapsed = time.perf_counter() - started
        release_clock(token)
        _wait_closed(server)
        server.shutdown()
        thread.join(1.0)
    assert elapsed < 2.0
    assert isinstance(payload, dict)
    assert str(payload.get("insights_fail") or "").startswith("insights_timeout")
    assert server.cancel_count >= 1
    assert server.open_count == 0


def test_flag_off_ignores_the_ask_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    server, thread = _serve()
    host, port = server.server_address
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "0.5")
    monkeypatch.delenv("DMS_ASK_LEAN", raising=False)
    monkeypatch.delenv("DMS_ASK_LEAN_CANCEL", raising=False)
    token = ensure_clock()
    note_deadline(time.monotonic() + 0.05)
    started = time.perf_counter()
    try:
        payload = compute_insights(
            f"http://{host}:{port}",
            question="how many rows",
            api_key="latency-probe-key",
            session_id="s",
            space_id="sp",
        )
    finally:
        elapsed = time.perf_counter() - started
        release_clock(token)
        _wait_closed(server)
        server.shutdown()
        thread.join(1.0)
    assert elapsed >= 0.35
    assert isinstance(payload, dict)
    assert str(payload.get("insights_fail") or "").startswith("insights_timeout")
    assert server.cancel_count == 0


def test_flag_off_timeout_does_not_post_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    server, thread = _serve()
    host, port = server.server_address
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "0.3")
    monkeypatch.delenv("DMS_ASK_LEAN", raising=False)
    monkeypatch.delenv("DMS_ASK_LEAN_CANCEL", raising=False)
    token = ensure_clock()
    try:
        payload = compute_insights(
            f"http://{host}:{port}",
            question="how many rows",
            api_key="latency-probe-key",
            session_id="s",
            space_id="sp",
        )
    finally:
        release_clock(token)
        _wait_closed(server)
        server.shutdown()
        thread.join(1.0)
    assert isinstance(payload, dict)
    assert str(payload.get("insights_fail") or "").startswith("insights_timeout")
    assert server.cancel_count == 0
    assert server.open_count == 0


def test_deferred_insights_keep_the_served_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping the second reading does not change the SQL or the rows."""
    _loop(monkeypatch)
    question = "Which locations are cold storage?"

    def fast(ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_COLD)

    monkeypatch.delenv("DMS_ASK_LEAN", raising=False)
    monkeypatch.delenv("DMS_ASK_LEAN_DEFER", raising=False)
    sync = _ask(tmp_path / "sync", question, fast)
    assert sync is not None and sync.get("abstained") is False

    calls = {"n": 0}

    def slow(ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] > 1:
            time.sleep(0.4)
        return _names(query_sql=_COLD)

    monkeypatch.setenv("DMS_ASK_LEAN_DEFER", "1")
    started = time.perf_counter()
    deferred = _ask(tmp_path / "defer", question, slow)
    elapsed = time.perf_counter() - started
    assert deferred is not None
    assert calls["n"] == 1
    assert elapsed < 0.35
    assert _served(deferred) == _served(sync)
    timings = deferred.get("timings")
    assert isinstance(timings, dict)
    assert "second_reading" in (timings.get("skipped") or [])


def test_trim_respects_the_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor import _insights_compute_seam

    question = "supplier audit"
    best = "- supplier audit date TIMESTAMP"
    others = [f"- supplier col{i} INTEGER " + ("m" * 400) for i in range(8)]
    schema = "DIALECT: duckdb\nSCHEMA\n" + "\n".join([*others, best])
    onto = {"schema_context": schema, "verified": True}
    monkeypatch.setenv("DMS_ASK_LEAN_TRIM", "1")
    monkeypatch.setenv("DMS_ASK_LEAN_TRIM_TOKENS", "40")

    class _Cortex:
        def __init__(self) -> None:
            self.onto: dict[str, Any] | None = None

        def compute_insights(self, asked: str, **kwargs: Any) -> dict[str, Any]:
            self.onto = kwargs.get("ontology")
            return {}

    cortex = _Cortex()
    _insights_compute_seam(
        cortex, question, session_id=None, space_id=None, ontology=dict(onto)
    )
    assert isinstance(cortex.onto, dict)
    text = str(cortex.onto.get("schema_context") or "")
    assert block_tokens("", "", cortex.onto) <= 40
    assert "audit date" in text
    assert "col0" not in text
    assert "col7" not in text


def test_timings_are_present_and_monotonic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    monkeypatch.delenv("DMS_ASK_LEAN", raising=False)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.02)
        return _names(query_sql=_COLD)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    timings = env.get("timings")
    assert isinstance(timings, dict)
    spans = timings.get("spans_ms")
    assert isinstance(spans, dict)
    for name in (
        "retrieve_context",
        "schema_context",
        "ground_defs",
        "tasks_choose",
        "insights_generate",
        "validate_explain",
        "submit_execute",
        "second_reading",
        "reconfirm_build",
        "total",
    ):
        assert name in spans
        assert spans[name] >= 0
    assert spans["insights_generate"] >= 15
    total = float(spans["total"])
    intervals = timings.get("intervals_ms")
    assert isinstance(intervals, list) and intervals
    for item in intervals:
        assert item["end_ms"] + 0.05 >= item["start_ms"] >= 0
        assert total + 1 >= item["end_ms"]
    prompts = timings.get("prompts")
    assert isinstance(prompts, list) and prompts
    assert prompts[0]["chars"] >= 0
    assert prompts[0]["est_tokens"] == (prompts[0]["chars"] + 3) // 4
    assert "text" not in prompts[0]


def test_flag_off_prompt_and_served_fields_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import _insights_compute_seam

    for name in (
        "DMS_ASK_LEAN",
        "DMS_ASK_LEAN_CANCEL",
        "DMS_ASK_LEAN_DEFER",
        "DMS_ASK_LEAN_TRIM",
        "DMS_ASK_LEAN_SINGLE",
    ):
        monkeypatch.delenv(name, raising=False)
    question = "supplier audit"
    schema = "DIALECT: duckdb\nSCHEMA\n" + "\n".join(
        f"- suppliers col{i} INTEGER " + ("m" * 80) for i in range(6)
    )
    onto = {"schema_context": schema, "verified": True, "governed_definitions": "keep me"}

    class _Cortex:
        def __init__(self) -> None:
            self.seen: list[tuple[str, str]] = []

        def compute_insights(self, asked: str, **kwargs: Any) -> dict[str, Any]:
            body = kwargs.get("ontology") or {}
            self.seen.append((asked, str(body.get("schema_context"))))
            return {}

    first = _Cortex()
    second = _Cortex()
    _insights_compute_seam(first, question, session_id=None, space_id=None, ontology=dict(onto))
    _insights_compute_seam(second, question, session_id=None, space_id=None, ontology=dict(onto))
    assert first.seen == second.seen
    assert first.seen[0][1] == schema
    assert "governed_definitions" not in first.seen[0][0] or "keep me" in first.seen[0][0]

    _loop(monkeypatch)

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_COLD)

    left = _ask(tmp_path / "a", "Which locations are cold storage?", compute)
    right = _ask(tmp_path / "b", "Which locations are cold storage?", compute)
    assert left is not None and right is not None
    assert _served(left) == _served(right)
    assert "timings" in left and "timings" in right
    # as_of is the wall clock. Two asks a second apart differ there only.
    drop = {"timings", "as_of"}
    bare_left = {key: value for key, value in left.items() if key not in drop}
    bare_right = {key: value for key, value in right.items() if key not in drop}
    assert bare_left == bare_right


def test_single_inflight_unless_voting(monkeypatch: pytest.MonkeyPatch) -> None:
    import contextvars

    monkeypatch.setenv("DMS_ASK_LEAN_SINGLE", "1")
    token = ensure_clock()
    try:
        state = {"open": 0, "max": 0}
        gate = threading.Lock()

        def work(voting: bool) -> None:
            with model_slot(voting=voting):
                with gate:
                    state["open"] += 1
                    state["max"] = max(state["max"], state["open"])
                time.sleep(0.06)
                with gate:
                    state["open"] -= 1

        threads = [
            threading.Thread(target=contextvars.copy_context().run, args=(work, False))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        assert state["max"] == 1

        state["max"] = 0
        threads = [
            threading.Thread(target=contextvars.copy_context().run, args=(work, True))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        assert state["max"] == 2
    finally:
        release_clock(token)


def test_stage_table_and_timeout_dominant_stage(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "verify"))
    from grade52 import format_stage_table

    records = [
        {
            "id": "a",
            "env": {
                "timings": {
                    "spans_ms": {"insights_generate": 80.0, "total": 90.0},
                    "model_calls": 2,
                    "abandoned": [{"leg": "insights_generate", "reason": "timeout"}],
                }
            },
        },
        {
            "id": "b",
            "timings": {
                "spans_ms": {"insights_generate": 20.0, "total": 25.0},
                "model_calls": 1,
                "abandoned": [],
            },
        },
    ]
    text = format_stage_table(records)
    assert "stage table n=2" in text
    assert "insights_generate" in text
    assert "abandoned=1" in text
    assert "calls_per_ask" in text

    spec = importlib.util.spec_from_file_location(
        "failure_cases_latency", ROOT / "scripts" / "verify" / "failure_cases.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    case = {"id": "a", "bucket": "ABSTAIN", "reason": "r", "abstain_code": "insights_timeout"}
    record = {
        "env": {
            "abstain_code": "insights_timeout",
            "timings": {
                "spans_ms": {
                    "insights_generate": 100.0,
                    "retrieve_context": 2.0,
                    "total": 110.0,
                }
            },
        }
    }
    row = mod.case_row(case, record, {"dms_sha": "abc"})
    assert "timeout" in row["failure_class"]
    assert row["dominant_stage"] == "insights_generate"
    plain = mod.case_row(
        {"id": "c", "bucket": "WRONG", "reason": "r"},
        {"env": {}},
        {"dms_sha": "abc"},
    )
    assert "dominant_stage" not in plain


def test_direct_spans_are_monotonic() -> None:
    token = ensure_clock()
    try:
        with span("retrieve_context"):
            time.sleep(0.01)
        from cortex_client.ask_clock import current_clock

        clock = current_clock()
        assert clock is not None
        snap = clock.snapshot()
    finally:
        release_clock(token)
    intervals = snap["intervals_ms"]
    assert intervals[0]["stage"] == "retrieve_context"
    assert intervals[0]["end_ms"] >= intervals[0]["start_ms"]
    assert snap["spans_ms"]["total"] >= snap["spans_ms"]["retrieve_context"]


def test_trim_subflag_off_keeps_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Master on, trim cut off: the schema block is not rewritten."""
    from dms_executor import _insights_compute_seam

    monkeypatch.setenv("DMS_ASK_LEAN", "1")
    monkeypatch.setenv("DMS_ASK_LEAN_TRIM", "0")
    schema = "DIALECT: duckdb\nSCHEMA\n- suppliers audit_date TIMESTAMP\n" + (
        "- suppliers col INTEGER " + ("m" * 400)
    )
    onto = {"schema_context": schema, "verified": True}

    class _Cortex:
        def __init__(self) -> None:
            self.schema = ""

        def compute_insights(self, asked: str, **kwargs: Any) -> dict[str, Any]:
            body = kwargs.get("ontology") or {}
            self.schema = str(body.get("schema_context") or "")
            return {}

    cortex = _Cortex()
    _insights_compute_seam(
        cortex, "supplier audit", session_id=None, space_id=None, ontology=dict(onto)
    )
    assert cortex.schema == schema
