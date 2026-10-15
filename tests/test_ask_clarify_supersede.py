"""ASK-CLARIFY-01 supersede of #419: the two reviewer majors, on the HTTP envelope.

1. The free-text clarify re-ask runs ``clarify_text``, so the compliance gate
   must see ``clarify_text`` (hard rule 8), not only ``body.question``.
2. Clarify stamps are per request. The Executor is a process singleton and the
   route runs on a thread pool, so a stamp held on it leaks across sessions.

Synthetic schema, stubbed model, no founder questions.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from cortex_client.gate import ComplianceDecision
from dms_executor.envelope import assert_envelope_valid
from test_ask_clarify_01 import (  # noqa: F401
    FINANCE,
    _bind_client,
    _two_measures,
    _Writer,
    wh,
)

DENY_MARKER = "qxdenied-marker-903"
PICK = "use-qxalpha771"
GROUND = ["qxalpha_fact", "qxbeta_fact"]
BASE = "show qxalpha771 and qxbeta771"


def _post(client: Any, **extra: Any) -> Any:
    body = {
        "question": BASE,
        "space_id": FINANCE,
        "session_id": "ses_http_a",
        "grounded_tables": GROUND,
    }
    body.update(extra)
    return client.post("/v1/chat/ask", json=body)


def _gate_denying_marker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A stand-in for Cortex F5 that denies any text carrying the marker."""
    import dms_api.routes.chat as chat_routes

    seen: list[str] = []

    def gate(*, action: str, metadata: dict[str, Any] | None = None, **_: Any) -> Any:
        text = str((metadata or {}).get("question") or "")
        seen.append(text)
        if DENY_MARKER in text:
            return ComplianceDecision(allowed=False, reason="policy_denied", action=action)
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr(chat_routes, "compliance_gate", gate)
    return seen


def test_reask_text_is_gated_not_only_body_question(
    wh: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    seen = _gate_denying_marker(monkeypatch)
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    try:
        first = _post(client)
        assert first.status_code == 200
        clarify = first.json()
        assert clarify["status"] == "clarify"
        asks_before = len(exe._cortex.asks)  # type: ignore[union-attr]

        # The direct ask with the marker is refused. This is the control.
        direct = _post(client, question=f"{BASE} {DENY_MARKER}")
        assert direct.status_code == 403

        # The same marker smuggled in clarify_text must be refused the same way,
        # and nothing may reach Cortex.
        denied = _post(
            client,
            clarify_id=clarify["clarify_id"],
            clarify_text=f"{PICK} {DENY_MARKER}",
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "policy_denied"
        assert len(exe._cortex.asks) == asks_before  # type: ignore[union-attr]

        # A clean re-ask is gated on the text that runs, and then serves.
        seen.clear()
        ok = _post(client, clarify_id=clarify["clarify_id"], clarify_text=PICK)
        assert ok.status_code == 200
        env = ok.json()
        assert_envelope_valid(env)
        assert PICK in seen
        assert env["clarify_reask"] is True
        assert env["clarify_parent_id"] == clarify["clarify_id"]
        assert env["abstained"] is False
        assert env["rows"] == [{"n": 4}]
        assert "4" in env["text"]
    finally:
        exe.close()


def test_option_pick_ignores_clarify_text_for_the_gate(
    wh: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """With option_id the text is not run, so it is not what the gate judges."""
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    seen = _gate_denying_marker(monkeypatch)
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    try:
        clarify = _post(client).json()
        seen.clear()
        res = _post(
            client,
            clarify_id=clarify["clarify_id"],
            option_id="opt_a",
            clarify_text=DENY_MARKER,
        )
        assert res.status_code == 200
        assert seen == [BASE]
        assert_envelope_valid(res.json())
    finally:
        exe.close()


def test_clarify_stamps_do_not_leak_across_concurrent_sessions(
    wh: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    _gate_denying_marker(monkeypatch)
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    cortex = exe._cortex
    assert cortex is not None
    real_ask = cortex.ask
    a_at_ask = threading.Event()
    b_done = threading.Event()

    def ask(req: Any) -> Any:
        if PICK in req.question:
            # Session A's re-ask is resolved and is now inside the pipeline.
            a_at_ask.set()
            assert b_done.wait(20)
        else:
            # Session B's plain ask runs while A is in flight.
            assert a_at_ask.wait(20)
        return real_ask(req)

    cortex.ask = ask  # type: ignore[method-assign]
    out: dict[str, Any] = {}
    try:
        clarify = _post(client).json()
        assert clarify["status"] == "clarify"

        def run_a() -> None:
            out["a"] = _post(
                client,
                clarify_id=clarify["clarify_id"],
                clarify_text=PICK,
            )

        def run_b() -> None:
            try:
                out["b"] = _post(
                    client,
                    question="show qxdelta771",
                    session_id="ses_http_b",
                    grounded_tables=["qxdelta_fact"],
                )
            finally:
                b_done.set()

        ta = threading.Thread(target=run_a)
        tb = threading.Thread(target=run_b)
        ta.start()
        tb.start()
        ta.join(60)
        tb.join(60)
        assert not ta.is_alive() and not tb.is_alive()

        a = out["a"].json()
        b = out["b"].json()
        assert_envelope_valid(a)
        assert_envelope_valid(b)
        # B asked plainly. It owns no clarify stamp, not A's id.
        assert "clarify_reask" not in b
        assert "clarify_parent_id" not in b
        assert b["abstained"] is False
        # A keeps its own stamp, whatever B did in the meantime.
        assert a["clarify_reask"] is True
        assert a["clarify_parent_id"] == clarify["clarify_id"]
        assert a["rows"] == [{"n": 4}]
    finally:
        exe.close()


def test_executor_holds_no_per_request_clarify_state(wh: Path) -> None:  # noqa: F811
    """Structural guard for the singleton: no per-request attribute on it."""
    from dms_executor import Executor

    exe = Executor(clarify_model=None)
    try:
        assert not hasattr(exe, "_clarify_reask_parent")
        assert not hasattr(exe, "_clarify_skipped")
    finally:
        exe.close()


def test_flag_off_clarify_fields_cannot_bypass_the_gate(
    wh: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Flag off: body.question runs. A denied one stays denied whatever clarify says."""
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    seen = _gate_denying_marker(monkeypatch)
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    try:
        res = _post(
            client,
            question=f"{BASE} {DENY_MARKER}",
            clarify_id="clr_unused",
            clarify_text=PICK,
        )
        assert res.status_code == 403
        assert exe._cortex.asks == []  # type: ignore[union-attr]
        assert f"{BASE} {DENY_MARKER}" in seen
    finally:
        exe.close()
