"""STUDIO-FIX-C. Reserve ask-back time when DMS_DEADLINE_ASKBACK=1.

Flag off keeps today's serving-deadline abstain. The flag does not read
the question text and does not move the lake-query interrupt.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from cortex_client.ask_clock import (
    ASKBACK_RESERVE_S,
    AskClock,
    askback_window_open,
    deadline_askback_enabled,
    ensure_clock,
    generate_budget_left_s,
    note_deadline,
    release_clock,
)


def test_flag_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("DMS_DEADLINE_ASKBACK", raising=False)
    assert deadline_askback_enabled() is False
    assert generate_budget_left_s() is None
    assert askback_window_open() is False
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + 1.0)
        assert generate_budget_left_s() is None
        assert askback_window_open() is False
    finally:
        release_clock(token)


def test_flag_zero_matches_unset(monkeypatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "0")
    assert deadline_askback_enabled() is False
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + ASKBACK_RESERVE_S)
        assert generate_budget_left_s() is None
    finally:
        release_clock(token)


def test_reserve_stops_a_new_generate_leg_and_opens_the_window(monkeypatch) -> None:
    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + ASKBACK_RESERVE_S + 5.0)
        left = generate_budget_left_s()
        assert left is not None and left > 4.0
        assert askback_window_open() is False
        note_deadline(time.monotonic() + (ASKBACK_RESERVE_S / 2))
        assert generate_budget_left_s() == 0.0
        assert askback_window_open() is True
        note_deadline(time.monotonic() - 1.0)
        assert generate_budget_left_s() == 0.0
        assert askback_window_open() is False
    finally:
        release_clock(token)


def test_lake_interrupt_stays_on_the_full_deadline(monkeypatch) -> None:
    """A query that already holds a connection is not cut at the reserve."""
    from dms_executor.lake_registry import AskControl

    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    monkeypatch.delenv("DMS_LAKE_SERVING_DEADLINE_DELIVER_S", raising=False)
    ctrl = AskControl()
    # 2.5s left. Deliver margin is 0.90s, so the lake interrupt is not due.
    # Subtracting the 2s reserve as well would make it due.
    ctrl.deadline = time.monotonic() + 2.5
    assert ctrl._interrupt_due() is False
    ctrl.deadline = time.monotonic() + 0.2
    assert ctrl._interrupt_due() is True


def test_suggestion_uses_the_reserve_only_after_generate_stops(monkeypatch) -> None:
    """The call cap still blocks a suggestion while generate has time left."""
    from cortex_client.compute import begin_answer_model_calls, insights_call_cap, note_model_call
    from dms_executor.reconfirm import complete_with_model

    monkeypatch.delenv("DMS_DEADLINE_ASKBACK", raising=False)
    begin_answer_model_calls()
    for _ in range(insights_call_cap()):
        note_model_call()
    seen: list[str] = []

    class _Cortex:
        def complete(self, prompt: str) -> str:
            seen.append(prompt)
            return "Which calendar month should I use"

    assert complete_with_model(_Cortex(), "prompt") == ""
    assert seen == []

    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + 30.0)
        assert askback_window_open() is False
        assert complete_with_model(_Cortex(), "prompt") == ""
        assert seen == []
        note_deadline(time.monotonic() + 1.0)
        assert complete_with_model(_Cortex(), "prompt") == "Which calendar month should I use"
        assert seen == ["prompt"]
    finally:
        release_clock(token)


def test_flag_off_sends_the_same_prompt_as_explicit_zero(monkeypatch) -> None:
    from dms_executor import _insights_compute_seam

    sent: dict[str, list[tuple[str, bool]]] = {}

    class _Cortex:
        def compute_insights(self, question: str, **kwargs: object) -> dict[str, object]:
            key = os.environ.get("DMS_DEADLINE_ASKBACK", "")
            sent.setdefault(key, []).append((question, "timeout" in kwargs))
            return {"unsure": True}

    question = "sentinel period total"
    for raw in ("", "0"):
        if raw:
            monkeypatch.setenv("DMS_DEADLINE_ASKBACK", raw)
        else:
            monkeypatch.delenv("DMS_DEADLINE_ASKBACK", raising=False)
        _insights_compute_seam(
            _Cortex(),
            question,
            session_id="ses",
            space_id=None,
            ontology={"sql_vote_prompt": question, "sql_vote_kind": "direct"},
        )
    assert sent[""] == sent["0"]
    assert sent[""][0][1] is False
    assert sent[""][0][0] == question


def test_flag_on_with_time_left_keeps_the_prompt_and_stops_at_the_reserve(monkeypatch) -> None:
    from dms_executor import _insights_compute_seam

    seen: list[tuple[str, bool]] = []

    class _Cortex:
        def compute_insights(self, question: str, **kwargs: object) -> dict[str, object]:
            seen.append((question, "timeout" in kwargs))
            return {"unsure": True}

    question = "sentinel period total"
    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + 30.0)
        _insights_compute_seam(
            _Cortex(),
            question,
            session_id="ses",
            space_id=None,
            ontology={"sql_vote_prompt": question, "sql_vote_kind": "direct"},
        )
        assert seen == [(question, True)]
        note_deadline(time.monotonic() + 0.4)
        missed = _insights_compute_seam(
            _Cortex(),
            question,
            session_id="ses",
            space_id=None,
            ontology={"sql_vote_prompt": question, "sql_vote_kind": "direct"},
        )
        assert missed == {"unsure": True}
        assert len(seen) == 1
    finally:
        release_clock(token)


def test_helpers_do_not_read_question_text() -> None:
    import inspect

    from cortex_client import ask_clock

    src = inspect.getsource(ask_clock.deadline_askback_enabled)
    src += inspect.getsource(ask_clock.generate_budget_left_s)
    src += inspect.getsource(ask_clock.askback_window_open)
    assert "question" not in src
    assert "re.compile" not in src
    assert isinstance(AskClock, type)


_PARENT_DEADLINE_TEXT = (
    "I can't answer this right now: the ask ran out of "
    "time after a serving connection was granted (serving_deadline_exceeded). "
    "No figure was returned."
)
_SUGGESTION = "Which stock measure and which calendar month should I use"
_ASK = "sentinel period total"


class _SlowCortex:
    def __init__(self, *, sleep_s: float, suggest: bool) -> None:
        self.sleep_s = sleep_s
        self.suggest = suggest
        self.prompts: list[str] = []
        self.completions: list[str] = []

    def compute_insights(self, question: str, **kwargs: object) -> dict[str, Any]:
        self.prompts.append(question)
        if "timeout" not in kwargs:
            time.sleep(self.sleep_s)
        return {"unsure": True}

    def complete(self, prompt: str) -> str:
        self.completions.append(prompt)
        if not self.suggest:
            return ""
        return _SUGGESTION

    def ask(self, _req: Any) -> Any:
        from cortex_client.models import AskResponse

        return AskResponse(
            answer="No governed figure for that question.",
            badge="abstain",
            abstained=True,
            assumptions="fixture abstain",
            audit_id="aud_deadline",
            route="abstain",
        )

    def submit(self, _req: Any) -> Any:
        raise AssertionError("uncertified ask must not submit")

    def ledger_append(self, _req: Any) -> Any:
        raise AssertionError("uncertified ask must not append")

    def close(self) -> None:
        return None


def _http_app(tmp_path: Path, cortex: _SlowCortex, monkeypatch: Any) -> Any:
    from cortex_contract.execution import Manifest
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter, SessionAcl
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_SERVING_DEADLINE_S", "3")
    get_settings.cache_clear()
    db = tmp_path / "deadline.duckdb"
    ensure_demo_warehouse(db)
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-11-27T00:00:00+00:00",
            expires_at="2026-11-27T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    app.state.cortex = cortex
    return TestClient(app)


def test_flag_off_deadline_envelope_matches_the_parent_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from dms_executor.envelope import assert_envelope_valid

    monkeypatch.delenv("DMS_DEADLINE_ASKBACK", raising=False)
    cortex = _SlowCortex(sleep_s=4.0, suggest=False)
    client = _http_app(tmp_path, cortex, monkeypatch)
    resp = client.post("/v1/chat/ask", json={"question": _ASK, "session_id": "ses_off"})
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert_envelope_valid(body)
    assert body["answer_id"] == "ans_serving_deadline_exceeded"
    assert body["abstain_reason"] == "serving_deadline_exceeded"
    assert body["text"] == _PARENT_DEADLINE_TEXT
    assert body["rows"] == []
    assert body["values"] == []
    assert body.get("sql_used") in (None, "")
    assert body["serving_deadline_s"] == 3.0
    assert body["route"] == "abstain"
    offer = body.get("reconfirm")
    if isinstance(offer, dict):
        assert not str(offer.get("suggested_question") or "").strip()
    assert cortex.prompts, "flag off must still send the generate prompt"


def test_flag_on_reserves_a_reconfirm_card(tmp_path: Path, monkeypatch: Any) -> None:
    from dms_executor.envelope import assert_envelope_valid

    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", "1")
    cortex = _SlowCortex(sleep_s=4.0, suggest=True)
    client = _http_app(tmp_path, cortex, monkeypatch)
    resp = client.post("/v1/chat/ask", json={"question": _ASK, "session_id": "ses_on"})
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert_envelope_valid(body)
    assert body["answer_id"] != "ans_serving_deadline_exceeded"
    assert body["abstained"] is True
    assert body["rows"] == []
    assert body["values"] == []
    assert body.get("sql_used") in (None, "")
    text = str(body.get("text") or "")
    assert not any(ch.isdigit() for ch in text)
    offer = body["reconfirm"]
    assert isinstance(offer["reason_code"], str) and offer["reason_code"].strip()
    assert offer["reason_code"] != "serving_deadline_exceeded"
    assert isinstance(offer["token"], str) and offer["token"]
    assert offer["suggested_question"] == _SUGGESTION
    assert not any(ch.isdigit() for ch in _SUGGESTION)
    assert cortex.completions
    assert "Failure reason:" in cortex.completions[0]


_COLD = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"


def _serve_cold(
    tmp_path: Path, monkeypatch: Any, *, flag: str
) -> tuple[dict[str, Any], list[str]]:
    from types import SimpleNamespace

    import duckdb
    from cortex_client.compute import begin_answer_model_calls
    from dms_executor import _insights_compute_seam
    from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
    from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
    from dms_executor.ontology import demo_ontology

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_DEADLINE_ASKBACK", flag)
    db = tmp_path / f"cold-{flag}.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        for i in range(24):
            code = f"WH-Z{i}"
            con.execute(
                "INSERT INTO locations VALUES (?, ?, 1, 1, ?, TRUE, ?)",
                [code, code, code, f"CAM-{i}"],
            )
        expected = con.execute(
            "SELECT location_code FROM locations WHERE is_cold_storage = TRUE "
            "ORDER BY location_code"
        ).fetchall()
    finally:
        con.close()
    codes = [str(row[0]) for row in expected]

    class _Cortex:
        def compute_insights(self, _question: str, **_kwargs: object) -> dict[str, Any]:
            return {
                "query_sql": _COLD,
                "served_model": "fake",
                "served_provider": "groq",
                "phase": "generate",
            }

    def compute(ctx: dict[str, Any]) -> dict[str, Any] | None:
        return _insights_compute_seam(
            _Cortex(),
            "which locations are cold storage",
            session_id="ses_cold",
            space_id=None,
            ontology=ctx,
        )

    def submit(sql: str) -> Any:
        held = duckdb.connect(str(db))
        try:
            cur = held.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            held.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_cold", output={"rows": rows})

    begin_answer_model_calls()
    token = ensure_clock()
    try:
        note_deadline(time.monotonic() + 60.0)
        env = maybe_generative_ask(
            "which locations are cold storage",
            warehouse=db,
            grantable={"inventory", "locations", "transactions", "suppliers", "shipments"},
            compute=compute,
            submit=submit,
            ledger_append=lambda _p: SimpleNamespace(entry_id="led", hash="h"),
            ontology=load_verified_ontology(db, demo_ontology(db)),
            dialect=SERVING_DIALECT,
        )
    finally:
        release_clock(token)
    assert isinstance(env, dict)
    return env, codes


def test_flag_on_serves_a_timely_certified_answer_in_full(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from dms_executor.envelope import assert_envelope_valid

    off, off_codes = _serve_cold(tmp_path, monkeypatch, flag="0")
    on, on_codes = _serve_cold(tmp_path, monkeypatch, flag="1")
    assert off_codes == on_codes
    assert len(off_codes) == 25
    for env, codes in ((off, off_codes), (on, on_codes)):
        assert_envelope_valid(env)
        assert env["abstained"] is False
        assert env.get("sql_used")
        assert "LIMIT" not in str(env["sql_used"]).upper()
        got = sorted(str(row.get("location_code")) for row in env["rows"])
        assert got == codes
    assert off["rows"] == on["rows"]
    assert off["sql_used"] == on["sql_used"]
    assert off["badge"] == on["badge"]
