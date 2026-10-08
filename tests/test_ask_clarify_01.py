"""ASK-CLARIFY-01. Synthetic schema, stubbed model. No founder questions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.clarify_stats import reset as reset_stats
from dms_core.clarify_stats import snapshot
from dms_executor import Executor
from dms_executor.ask_clarify import extra_model_calls, reset_extra_model_calls
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

_MP: pytest.MonkeyPatch | None = None

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
CLOCK = "2026-10-08T00:00:00Z"

_GRANTED = (
    "qxalpha_fact",
    "qxbeta_fact",
    "qxgamma_fact",
    "qxdelta_fact",
    "qxepsilon_fact",
    "qxleak_fact",
)


def _minter() -> ManifestMinter:
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
            issued_at=CLOCK,
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


@dataclass
class _Cortex:
    computes: list[str] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.computes.append(question)
        return {}

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run_clarify")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_clarify", hash="hash_clarify")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="qx synthetic total is 4.",
            badge="certified",
            sql_used="SELECT 4",
            rows=[{"n": 4}],
            assumptions="fixture",
            audit_id="aud_clarify",
            route="sql",
        )


class _Writer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.payload


def _seed(path: Path) -> None:
    ensure_demo_warehouse(path)
    con = connect_file(path)
    try:
        con.execute("CREATE TABLE qxalpha_fact (qxalpha771 DOUBLE)")
        con.execute("INSERT INTO qxalpha_fact VALUES (1)")
        con.execute("CREATE TABLE qxbeta_fact (qxbeta771 DOUBLE)")
        con.execute("INSERT INTO qxbeta_fact VALUES (2)")
        con.execute("CREATE TABLE qxgamma_fact (qxgamma771 DOUBLE, event_date DATE)")
        con.execute("INSERT INTO qxgamma_fact VALUES (3, DATE '2024-01-15')")
        con.execute("CREATE TABLE qxdelta_fact (qxdelta771 DOUBLE)")
        con.execute("INSERT INTO qxdelta_fact VALUES (4)")
        con.execute("CREATE TABLE qxepsilon_fact (qxepsilon771 DOUBLE)")
        con.execute("INSERT INTO qxepsilon_fact VALUES (5)")
        con.execute("CREATE TABLE qxleak_fact (qxleak771 DOUBLE, email VARCHAR)")
        con.execute("INSERT INTO qxleak_fact VALUES (6, 'uncleared-token-441')")
        con.execute("CREATE TABLE qxhidden_fact (qxhidden_note VARCHAR)")
        con.execute("INSERT INTO qxhidden_fact VALUES ('ungranted-token-552')")
    finally:
        con.close()


def _executor(path: Path, writer: _Writer | None, cortex: _Cortex) -> Executor:
    return Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=_minter(),
        warehouse_path=path,
        session_store=DemoSessionStore(
            extra_grants=_GRANTED,
            uploads=lambda: (),
            warehouse=path,
        ),
        clarify_model=writer,
    )


def _opt(oid: str, label: str, binding: dict[str, Any]) -> dict[str, Any]:
    return {"id": oid, "label": label, "binding": binding}


def _measure(name: str, table: str) -> dict[str, Any]:
    return {"kind": "measure", "name": name, "table": table}


def _two_measures() -> dict[str, Any]:
    return {
        "question": "which-qx-measure-881",
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "options": [
            _opt("opt_a", "use-qxalpha771", _measure("qxalpha771", "qxalpha_fact")),
            _opt("opt_b", "use-qxbeta771", _measure("qxbeta771", "qxbeta_fact")),
        ],
    }


@pytest.fixture
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    global _MP
    _MP = monkeypatch
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    monkeypatch.setattr("dms_executor.envelope._now", lambda: CLOCK)
    monkeypatch.setattr("dms_executor.datetime_now", lambda: CLOCK)
    reset_stats()
    reset_extra_model_calls()
    path = tmp_path / "clarify.duckdb"
    _seed(path)
    return path


def _ask(
    path: Path,
    question: str,
    tables: list[str],
    *,
    writer: _Writer | None,
    flag: bool,
    session_id: str = "ses_clarify",
    clarify_id: str | None = None,
    option_id: str | None = None,
    cortex: _Cortex | None = None,
) -> tuple[dict[str, Any], _Cortex, Executor]:
    assert _MP is not None
    if flag:
        _MP.setenv("DMS_ASK_CLARIFY", "1")
    else:
        _MP.delenv("DMS_ASK_CLARIFY", raising=False)
    held = cortex or _Cortex()
    exe = _executor(path, writer, held)
    env = exe.live_ask(
        question,
        space_id=FINANCE,
        session_id=session_id,
        tables=tables,
        clarify_id=clarify_id,
        option_id=option_id,
    )
    return env, held, exe


def test_two_close_measures_return_clarify(wh: Path) -> None:
    writer = _Writer(_two_measures())
    env, cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=writer,
        flag=True,
    )
    try:
        assert env["status"] == "clarify"
        assert env["badge"] == "ABSTAIN"
        assert env["abstained"] is True
        assert env["rows"] == []
        assert env["values"] == []
        assert len(env["options"]) == 2
        assert {opt["id"] for opt in env["options"]} == {"opt_a", "opt_b"}
        assert_envelope_valid(env)
        assert cortex.asks == []
    finally:
        exe.close()


def test_option_pick_serves_with_binding(wh: Path) -> None:
    writer = _Writer(_two_measures())
    env, cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=writer,
        flag=True,
    )
    try:
        picked = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=env["clarify_id"],
            option_id="opt_a",
        )
        assert picked.get("status") != "clarify"
        assert picked["abstained"] is False
        assert picked["rows"] == [{"n": 4}]
        assert cortex.asks
        assert '"name":"qxalpha771"' in cortex.asks[-1].question
        assert_envelope_valid(picked)
    finally:
        exe.close()


def test_missing_time_range_returns_clarify(wh: Path) -> None:
    payload = {
        "question": "which-qx-range-882",
        "prompt_tokens": 5,
        "completion_tokens": 4,
        "options": [
            _opt(
                "opt_q1",
                "range-qx-one",
                {
                    "kind": "time_range",
                    "table": "qxgamma_fact",
                    "column": "event_date",
                    "start": "2024-01-01",
                    "end": "2024-03-31",
                },
            ),
            _opt(
                "opt_q2",
                "range-qx-two",
                {
                    "kind": "time_range",
                    "table": "qxgamma_fact",
                    "column": "event_date",
                    "start": "2024-04-01",
                    "end": "2024-06-30",
                },
            ),
        ],
    }
    env, _cortex, exe = _ask(
        wh,
        "show qxgamma771",
        ["qxgamma_fact"],
        writer=_Writer(payload),
        flag=True,
    )
    try:
        assert env["status"] == "clarify"
        kinds = {opt["binding"]["kind"] for opt in env["options"]}
        assert kinds == {"time_range"}
    finally:
        exe.close()


def test_invalid_model_options_are_dropped(wh: Path) -> None:
    payload = _two_measures()
    payload["options"] = [
        _opt("opt_bad", "not-a-real-column", {"kind": "table", "name": "no_such_table"}),
        *_two_measures()["options"],
    ]
    env, _cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(payload),
        flag=True,
    )
    try:
        ids = {opt["id"] for opt in env["options"]}
        assert env["status"] == "clarify"
        assert "opt_bad" not in ids
        assert ids == {"opt_a", "opt_b"}
    finally:
        exe.close()


def test_ungranted_and_uncleared_options_are_dropped(wh: Path) -> None:
    payload = {
        "question": "which-qx-measure-883",
        "prompt_tokens": 9,
        "completion_tokens": 3,
        "options": [
            _opt("opt_a", "use-qxalpha771", _measure("qxalpha771", "qxalpha_fact")),
            _opt("opt_l", "use-qxleak771", _measure("qxleak771", "qxleak_fact")),
            _opt(
                "opt_hidden",
                "ungranted-token-552",
                {"kind": "table", "name": "qxhidden_fact"},
            ),
            _opt(
                "opt_mail",
                "uncleared-token-441",
                {"kind": "column", "table": "qxleak_fact", "name": "email"},
            ),
        ],
    }
    writer = _Writer(payload)
    env, _cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxleak771",
        ["qxalpha_fact", "qxleak_fact"],
        writer=writer,
        flag=True,
    )
    try:
        assert writer.prompts
        prompt = writer.prompts[0]
        assert "qxhidden_fact" not in prompt
        assert "ungranted-token-552" not in prompt
        assert "uncleared-token-441" not in prompt
        assert "email" not in prompt
        ids = {opt["id"] for opt in env["options"]}
        assert env["status"] == "clarify"
        assert ids == {"opt_a", "opt_l"}
        blob = json.dumps(env["options"])
        assert "ungranted-token-552" not in blob
        assert "uncleared-token-441" not in blob
        assert "qxhidden_fact" not in blob
    finally:
        exe.close()


def test_expired_clarify_id_is_named_abstain(wh: Path) -> None:
    env, _cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
    )
    try:
        cid = env["clarify_id"]
        exe._clarify_attempts[cid]["created"] = 0
        expired = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=cid,
            option_id="opt_a",
        )
        unknown = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id="clr_missing",
            option_id="opt_a",
        )
        assert expired["abstain_reason"] == "clarify_expired"
        assert expired["abstained"] is True
        assert expired["rows"] == []
        assert unknown["abstain_reason"] == "clarify_unknown"
        assert_envelope_valid(expired)
        assert_envelope_valid(unknown)
    finally:
        exe.close()


def test_expired_clarify_id_http_200(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    import dms_executor
    from dms_api.app import create_app
    from dms_api.deps import get_settings
    from dms_api.settings import Settings

    cortex = _Cortex()
    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    try:
        app = create_app()
    finally:
        dms_executor.probe_openvault = previous
    exe = _executor(wh, _Writer(_two_measures()), cortex)
    app.state.ask_service = exe
    app.state.cortex = cortex
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=False,
        dms_mcp=False,
    )
    client = TestClient(app)
    try:
        first = client.post(
            "/v1/chat/ask",
            json={
                "question": "show qxalpha771 and qxbeta771",
                "space_id": FINANCE,
                "session_id": "ses_http",
                "grounded_tables": ["qxalpha_fact", "qxbeta_fact"],
            },
        )
        assert first.status_code == 200
        body = first.json()
        assert body["status"] == "clarify"
        exe._clarify_attempts[body["clarify_id"]]["created"] = 0
        second = client.post(
            "/v1/chat/ask",
            json={
                "question": "show qxalpha771 and qxbeta771",
                "space_id": FINANCE,
                "session_id": "ses_http",
                "grounded_tables": ["qxalpha_fact", "qxbeta_fact"],
                "clarify_id": body["clarify_id"],
                "option_id": "opt_a",
            },
        )
        assert second.status_code == 200
        assert second.json()["abstain_reason"] == "clarify_expired"
        missing = client.post(
            "/v1/chat/ask",
            json={
                "question": "show qxalpha771 and qxbeta771",
                "space_id": FINANCE,
                "session_id": "ses_http",
                "grounded_tables": ["qxalpha_fact", "qxbeta_fact"],
                "clarify_id": "clr_missing",
                "option_id": "opt_a",
            },
        )
        assert missing.status_code == 200
        assert missing.json()["abstain_reason"] == "clarify_unknown"
    finally:
        exe.close()


def test_flag_off_does_not_clarify(wh: Path) -> None:
    writer = _Writer(_two_measures())
    env, cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=writer,
        flag=False,
    )
    try:
        assert env.get("status") != "clarify"
        assert writer.prompts == []
        assert extra_model_calls() == 0
        assert env["rows"] == [{"n": 4}]
        assert cortex.asks
    finally:
        exe.close()


def test_clear_asks_byte_equal_with_flag_on(wh: Path) -> None:
    asks = (
        ("show qxdelta771", ["qxdelta_fact"], "ses_delta"),
        ("show qxepsilon771", ["qxepsilon_fact"], "ses_epsilon"),
    )
    for question, tables, session_id in asks:
        writer = _Writer(_two_measures())
        off, _c1, exe1 = _ask(
            wh, question, tables, writer=writer, flag=False, session_id=session_id
        )
        exe1.close()
        assert writer.prompts == []
        on, _c2, exe2 = _ask(
            wh, question, tables, writer=writer, flag=True, session_id=session_id
        )
        exe2.close()
        assert on.get("status") != "clarify"
        assert writer.prompts == []
        assert off["abstained"] is False
        assert on == off


def test_clarify_envelope_is_not_served(wh: Path) -> None:
    reset_stats()
    env, _cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
    )
    try:
        assert env["status"] == "clarify"
        assert env["rows"] == []
        assert not any(str(key).startswith("served_") for key in env)
        rates = snapshot()
        assert rates["clarify"] == 1
        assert rates["abstain"] == 0
        assert "clarify_rate" in rates
        assert "abstain_rate" in rates
    finally:
        exe.close()


def test_clear_ask_makes_no_extra_model_call(wh: Path) -> None:
    writer = _Writer(_two_measures())
    reset_extra_model_calls()
    _off, off_cortex, exe1 = _ask(
        wh,
        "show qxdelta771",
        ["qxdelta_fact"],
        writer=writer,
        flag=False,
        session_id="ses_cost_off",
    )
    exe1.close()
    reset_extra_model_calls()
    _on, on_cortex, exe2 = _ask(
        wh,
        "show qxdelta771",
        ["qxdelta_fact"],
        writer=writer,
        flag=True,
        session_id="ses_cost_on",
    )
    exe2.close()
    assert writer.prompts == []
    assert extra_model_calls() == 0
    assert len(on_cortex.computes) == len(off_cortex.computes)


def test_ambiguous_ask_makes_at_most_one_extra_model_call(wh: Path) -> None:
    writer = _Writer(_two_measures())
    reset_extra_model_calls()
    _env, cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=writer,
        flag=True,
    )
    try:
        assert len(writer.prompts) == 1
        assert extra_model_calls() == 1
        assert cortex.computes == []
    finally:
        exe.close()


def test_clarify_records_tokens_and_rates(wh: Path) -> None:
    reset_stats()
    reset_extra_model_calls()
    env, _cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        session_id="ses_tokens",
    )
    try:
        cid = env["clarify_id"]
        attempt = exe._clarify_attempts[cid]
        assert attempt["prompt_tokens"] == 11
        assert attempt["completion_tokens"] == 7
        assert env["clarify_prompt_tokens"] == 11
        assert env["clarify_completion_tokens"] == 7
        rates = snapshot()
        assert rates["clarify_rate"] == 1.0
        assert rates["abstain_rate"] == 0.0
        assert rates["tokens_per_clarify"] == 18.0
    finally:
        exe.close()
