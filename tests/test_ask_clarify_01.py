"""ASK-CLARIFY-01. Synthetic schema, stubbed model. No founder questions."""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
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
    submits: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.computes.append(question)
        return {}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
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
    clarify_text: str | None = None,
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
        clarify_text=clarify_text,
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


def test_free_text_outside_options_abstains(wh: Path) -> None:
    writer = _Writer(_two_measures())
    cortex = _Cortex()
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=writer,
        flag=True,
        cortex=cortex,
    )
    try:
        cid = env["clarify_id"]
        reset_extra_model_calls()
        picked = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=cid,
            clarify_text="use qxhidden_fact ungranted-token-552",
        )
        assert picked["abstain_reason"] == "ungranted_table"
        assert "qxhidden_fact" not in json.dumps(picked)
        assert picked["abstained"] is True
        assert picked["rows"] == []
        assert picked["values"] == []
        assert picked.get("status") != "clarify"
        assert picked["clarify_reask"] is True
        assert picked["clarify_parent_id"] == cid
        assert cortex.submits == []
        assert cortex.asks == []
        assert len(writer.prompts) == 1
        assert extra_model_calls() == 0
        assert cid not in exe._clarify_attempts
        rates = snapshot()
        assert rates["asks"] == 1
        assert rates["clarify"] == 0
        assert rates["abstain"] == 1
        assert_envelope_valid(picked)
    finally:
        exe.close()


_MAIN_SHA = "57d85c529aa363825aaa566f12b8822fd76a4215"
_REPO = Path(__file__).resolve().parents[1]
_MAIN_TREE = Path("/tmp/dms-57d85c52")


def _letter_name() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return "qx" + "".join(secrets.choice(alphabet) for _ in range(24))


def _hide_table(path: Path, name: str) -> None:
    con = connect_file(path)
    try:
        con.execute(f"CREATE TABLE {name} (c VARCHAR)")
        con.execute(f"INSERT INTO {name} VALUES ('z')")
    finally:
        con.close()


def _pin_as_of(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<as_of>" if key == "as_of" else _pin_as_of(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_pin_as_of(item) for item in value]
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        _pin_as_of(value), sort_keys=True, separators=(",", ":"), default=str
    ).encode()


def _stream_body(
    client: TestClient, payload: dict[str, Any]
) -> tuple[int, list[bytes], list[tuple[str, str]]]:
    # No `with TestClient`: lifespan would replace the injected executor.
    with client.stream("POST", "/v1/chat/ask", json=payload) as response:
        chunks = list(response.iter_bytes())
        headers = list(response.headers.items())
        return response.status_code, chunks, headers


def _name_leaked(name: str, chunks: list[bytes], headers: list[tuple[str, str]]) -> str:
    folded = name.casefold()
    for index, chunk in enumerate(chunks):
        if folded in chunk.decode("utf-8", "replace").casefold():
            return f"chunk {index}"
    if folded in b"".join(chunks).decode("utf-8", "replace").casefold():
        return "joined body"
    for key, value in headers:
        if folded in f"{key}: {value}".casefold():
            return f"header {key}"
    return ""


def _bind_client(path: Path, writer: _Writer) -> tuple[TestClient, Executor]:
    import dms_executor
    from dms_api.app import create_app
    from dms_api.deps import get_settings
    from dms_api.settings import Settings

    cortex = _Cortex()
    previous = getattr(dms_executor, "probe_openvault", None)
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    try:
        app = create_app()
    finally:
        if previous is not None:
            dms_executor.probe_openvault = previous  # type: ignore[method-assign]
    exe = _executor(path, writer, cortex)
    app.state.ask_service = exe
    app.state.cortex = cortex
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=False,
        dms_mcp=False,
    )
    return TestClient(app), exe


def _ticket_name_only(caplog: pytest.LogCaptureFixture, name: str) -> None:
    folded = name.casefold()
    for rec in caplog.records:
        blob = rec.getMessage()
        if rec.args:
            blob = f"{blob} {rec.args!r}"
        if folded not in blob.casefold():
            continue
        assert rec.name == "dms_executor.pipeline_failure", rec.name
        assert rec.levelno == logging.WARNING
        assert rec.getMessage().startswith("pipeline_failure ")


def _main_tree() -> Path:
    if _MAIN_TREE.exists():
        probe = subprocess.run(
            ["git", "-C", str(_MAIN_TREE), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.stdout.strip() == _MAIN_SHA:
            return _MAIN_TREE
        subprocess.run(
            ["git", "-C", str(_REPO), "worktree", "remove", "--force", str(_MAIN_TREE)],
            check=False,
        )
    cat = subprocess.run(
        ["git", "-C", str(_REPO), "cat-file", "-e", f"{_MAIN_SHA}^{{commit}}"],
        check=False,
    )
    if cat.returncode != 0:
        subprocess.run(
            ["git", "-C", str(_REPO), "fetch", "--depth=1", "origin", _MAIN_SHA],
            check=True,
        )
    subprocess.run(
        ["git", "-C", str(_REPO), "worktree", "add", "--detach", str(_MAIN_TREE), _MAIN_SHA],
        check=True,
    )
    return _MAIN_TREE


_FLAG_OFF_MAIN = r"""
import json, os, sys
from pathlib import Path

root = Path(sys.argv[1])
payload = json.loads(sys.argv[2])
warehouse = sys.argv[3]
sys.meta_path[:] = [
    item for item in sys.meta_path
    if "editable" not in getattr(item, "__module__", "")
]
sys.path[:] = [item for item in sys.path if not str(item).startswith(str(Path.cwd()))]
for rel in (
    "apps/api",
    "packages/core",
    "packages/cortex_client",
    "packages/executor",
    "packages/ledger",
):
    sys.path.insert(0, str(root / rel))
os.environ.pop("DMS_ASK_CLARIFY", None)
os.environ.pop("DMS_CLOOP_B", None)
os.environ["DMS_DEMO_FALLBACK"] = "0"

import dms_executor
import dms_executor.envelope as envelope
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.deps import get_settings
from dms_api.settings import Settings
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.manifest import ManifestMinter
from fastapi.testclient import TestClient

CLOCK = "2026-10-08T00:00:00Z"
envelope._now = lambda: CLOCK
dms_executor.datetime_now = lambda: CLOCK
sys.stderr.write(str(dms_executor.__file__) + "\n")

class _Cortex:
    def compute_insights(self, question, **_kwargs):
        return {}
    def submit(self, req):
        return QueryResult(ok=True, status="bound", run_id="run_clarify")
    def ledger_append(self, req):
        return LedgerAppendResponse(entry_id="led_clarify", hash="hash_clarify")
    def ask(self, req):
        return AskResponse(
            answer="qx synthetic total is 4.",
            badge="certified",
            sql_used="SELECT 4",
            rows=[{"n": 4}],
            assumptions="fixture",
            audit_id="aud_clarify",
            route="sql",
        )

minter = ManifestMinter()

def _mint(acl):
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

minter.mint_manifest = _mint
minter.fetch_intermediate = lambda: None
minter.close = lambda: None
minter.invalidate = lambda *_a, **_k: None
granted = (
    "qxalpha_fact", "qxbeta_fact", "qxgamma_fact",
    "qxdelta_fact", "qxepsilon_fact", "qxleak_fact",
)
previous = getattr(dms_executor, "probe_openvault", None)
dms_executor.probe_openvault = lambda **_k: (None, "off")
try:
    app = create_app()
finally:
    if previous is not None:
        dms_executor.probe_openvault = previous
exe = dms_executor.Executor(
    cortex=_Cortex(),
    minter=minter,
    warehouse_path=warehouse,
    session_store=DemoSessionStore(
        extra_grants=granted,
        uploads=lambda: (),
        warehouse=Path(warehouse),
    ),
)
app.state.ask_service = exe
app.state.cortex = exe._cortex
app.dependency_overrides[get_settings] = lambda: Settings(
    _env_file=None,
    dms_ask_mode="live",
    dms_demo_fallback=False,
    dms_harness_ask_paths=False,
    dms_mcp=False,
)
client = TestClient(app)
try:
    response = client.post("/v1/chat/ask", json=payload)
finally:
    exe.close()
if response.status_code != 200:
    sys.stderr.write(response.text[:800])
    raise SystemExit(response.status_code)
json.dump(response.json(), sys.stdout)
"""


def _main_flag_off(payload: dict[str, Any], warehouse: Path) -> dict[str, Any]:
    tree = _main_tree()
    script = warehouse.parent / "flag_off_main.py"
    script.write_text(_FLAG_OFF_MAIN, encoding="utf-8")
    env = os.environ.copy()
    env.pop("DMS_ASK_CLARIFY", None)
    env.pop("DMS_CLOOP_B", None)
    env["DMS_DEMO_FALLBACK"] = "0"
    # Drop the checkout's PYTHONPATH so the worktree wins after the finder strip.
    env["PYTHONPATH"] = ""
    got = subprocess.run(
        [sys.executable, str(script), str(tree), json.dumps(payload), str(warehouse)],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert got.returncode == 0, got.stderr[-800:]
    assert str(tree) in got.stderr, got.stderr
    body = json.loads(got.stdout)
    assert isinstance(body, dict)
    return body


def test_ungranted_name_absent_from_ask_http(
    wh: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Re-ask of a random ungranted table. The name is not in the HTTP body.

    Flag on: it may show up only on the WARNING pipeline_failure line.
    Flag off: the ask body matches main 57d85c52 once as_of is masked.
    """
    name = _letter_name()
    _hide_table(wh, name)
    reply = (
        f"use {name} \"{name.upper()}\" '{name}' "
        f"from main.{name} and main.\"{name.upper()}\""
    )
    question = "show qxalpha771 and qxbeta771"
    tables = ["qxalpha_fact", "qxbeta_fact"]
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    _arm_tickets(caplog, monkeypatch)
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    try:
        status, chunks, _headers = _stream_body(
            client,
            {
                "question": question,
                "space_id": FINANCE,
                "session_id": "ses_http_name",
                "grounded_tables": tables,
            },
        )
        assert status == 200, chunks
        opened = json.loads(b"".join(chunks))
        assert opened["status"] == "clarify"
        caplog.clear()
        status, chunks, headers = _stream_body(
            client,
            {
                "question": question,
                "space_id": FINANCE,
                "session_id": "ses_http_name",
                "grounded_tables": tables,
                "clarify_id": opened["clarify_id"],
                "clarify_text": reply,
            },
        )
        assert status == 200, chunks
        # Name scan first, so a leak fails before the flag-off compare.
        leaked = _name_leaked(name, chunks, headers)
        assert leaked == "", leaked
        body = json.loads(b"".join(chunks))
        assert body["abstain_reason"] == "ungranted_table"
        assert name.casefold() not in json.dumps(body["assumptions"]).casefold()
        _ticket_name_only(caplog, name)
    finally:
        exe.close()

    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    flag_payload = {
        "question": question,
        "space_id": FINANCE,
        "session_id": "ses_flag_off_name",
        "grounded_tables": tables,
        "clarify_id": "clrnotused",
        "clarify_text": reply,
    }
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    try:
        status, chunks, headers = _stream_body(client, flag_payload)
        assert status == 200, chunks
        leaked = _name_leaked(name, chunks, headers)
        assert leaked == "", leaked
        head_env = json.loads(b"".join(chunks))
    finally:
        exe.close()
    assert not any(str(key).startswith("clarify_") for key in head_env)
    main_env = _main_flag_off(flag_payload, wh)
    assert _canonical(head_env) == _canonical(main_env)


def test_ungranted_cccc_does_not_rewrite_space_id(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Table ``cccc`` must not eat the ``cccc`` runs inside the space id."""
    name = "cccc"
    _hide_table(wh, name)
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    client, exe = _bind_client(wh, _Writer(_two_measures()))
    try:
        status, chunks, _headers = _stream_body(
            client,
            {
                "question": "show qxalpha771 and qxbeta771",
                "space_id": FINANCE,
                "session_id": "ses_cccc_http",
                "grounded_tables": ["qxalpha_fact", "qxbeta_fact"],
            },
        )
        assert status == 200, chunks
        opened = json.loads(b"".join(chunks))
        assert opened["status"] == "clarify"
        status, chunks, _headers = _stream_body(
            client,
            {
                "question": "show qxalpha771 and qxbeta771",
                "space_id": FINANCE,
                "session_id": "ses_cccc_http",
                "grounded_tables": ["qxalpha_fact", "qxbeta_fact"],
                "clarify_id": opened["clarify_id"],
                "clarify_text": "use cccc",
            },
        )
        assert status == 200, chunks
        body = json.loads(b"".join(chunks))
    finally:
        exe.close()
    assert body["space_id"] == FINANCE
    assert body["session_id"] == "ses_cccc_http"
    assert body["abstain_reason"] == "ungranted_table"


def test_closed_tail_scrub_keeps_session_id_and_filter_cc() -> None:
    """``ungranted_table:sku`` must not rewrite ``ses_cc_sku`` or ``cc``."""
    from dms_executor.abstain import build_abstain

    env = build_abstain(
        reason="ungranted_table:sku",
        question="q",
        abstain_reason="ungranted_table:sku",
        answer_id="ans_ungranted_table:sku",
        text="ABSTAIN ungranted_table:sku",
        assumptions=["ungranted_table:sku", "filter sku=cc"],
        space_id=FINANCE,
        session_id="ses_cc_sku",
        rows=[],
        values=[],
        sql_used=None,
        ask_mode="live",
        route="abstain",
    )
    assert env["session_id"] == "ses_cc_sku"
    assert env["space_id"] == FINANCE
    assert "filter sku=cc" in env["assumptions"]
    assert env["answer_id"] == "ans_ungranted_table"
    assert env["abstain_reason"] == "ungranted_table"
    assert env["text"] == "ABSTAIN ungranted_table:sku"


def test_free_text_exact_label_serves(wh: Path) -> None:
    writer = _Writer(_two_measures())
    cortex = _Cortex()
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=writer,
        flag=True,
        cortex=cortex,
    )
    try:
        cid = env["clarify_id"]
        reset_extra_model_calls()
        picked = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=cid,
            clarify_text="use-qxalpha771",
        )
        assert picked.get("status") != "clarify"
        assert picked["clarify_reask"] is True
        assert picked["clarify_parent_id"] == cid
        assert picked["abstained"] is False
        assert picked["rows"] == [{"n": 4}]
        assert cortex.asks
        assert '"name":"qxalpha771"' not in cortex.asks[-1].question
        assert "use-qxalpha771" in cortex.asks[-1].question
        assert len(writer.prompts) == 1
        assert extra_model_calls() == 0
        rates = snapshot()
        assert rates["asks"] == 1
        assert rates["clarify"] == 0
        assert rates["abstain"] == 0
    finally:
        exe.close()


def test_unknown_option_id_abstains(wh: Path) -> None:
    cortex = _Cortex()
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        cortex=cortex,
    )
    try:
        picked = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=env["clarify_id"],
            option_id="opt_missing",
        )
        assert picked["abstain_reason"] == "clarify_option_unknown"
        assert picked["rows"] == []
        assert cortex.submits == []
        assert cortex.asks == []
    finally:
        exe.close()


def test_tampered_binding_abstains(wh: Path) -> None:
    cortex = _Cortex()
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        cortex=cortex,
    )
    try:
        cid = env["clarify_id"]
        exe._clarify_attempts[cid]["options"][0]["binding"] = {
            "kind": "table",
            "name": "qxhidden_fact",
        }
        picked = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=cid,
            option_id="opt_a",
        )
        assert picked["abstain_reason"] == "clarify_binding_ungranted"
        assert picked["abstained"] is True
        assert picked["rows"] == []
        assert picked["values"] == []
        assert cortex.submits == []
        assert cortex.asks == []
        assert picked.get("badge") != "L0_CERTIFIED"
        assert_envelope_valid(picked)
    finally:
        exe.close()


def test_time_range_dates_match_the_store(wh: Path) -> None:
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
        stored = exe._clarify_attempts[env["clarify_id"]]["options"]
        assert env["options"] == stored
        blob = json.dumps(env["options"])
        assert "2024-01-01" in blob
        assert "2024-03-31" in blob
        assert "2024-04-01" in blob
        assert "2024-06-30" in blob
        assert "DMSMASK_dob" not in blob
    finally:
        exe.close()


def test_clear_ask_does_not_sample(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def _spy(warehouse: Path, table: str, column: str) -> list[str]:
        seen.append((table, column))
        return []

    monkeypatch.setattr("dms_executor.ask_clarify._samples", _spy)
    env, _cortex, exe = _ask(
        wh,
        "show qxdelta771",
        ["qxdelta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        session_id="ses_nosample",
    )
    try:
        assert env["rows"] == [{"n": 4}]
        assert env.get("status") != "clarify"
        assert seen == []
    finally:
        exe.close()


def test_ambiguous_samples_skip_ungranted(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def _spy(warehouse: Path, table: str, column: str) -> list[str]:
        seen.append((table, column))
        return []

    monkeypatch.setattr("dms_executor.ask_clarify._samples", _spy)
    env, _cortex, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        session_id="ses_sample",
    )
    try:
        assert env["status"] == "clarify"
        assert seen
        assert all(table != "qxhidden_fact" for table, _col in seen)
        assert all(column != "email" for _table, column in seen)
    finally:
        exe.close()


def test_pii_raise_does_not_serve(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("detector down")

    monkeypatch.setattr("dms_executor.ask_clarify.column_is_pii", _boom)
    cortex = _Cortex()
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        cortex=cortex,
        session_id="ses_pii",
    )
    try:
        assert env["abstain_reason"] == "clarify_column_unreadable"
        assert env["abstained"] is True
        assert env["rows"] == []
        assert env["values"] == []
        assert env.get("status") != "clarify"
        assert cortex.submits == []
        assert cortex.asks == []
        assert_envelope_valid(env)
    finally:
        exe.close()


def _completion(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


class _Http:
    def __init__(self, posts: list[dict[str, Any]], *, fail: bool) -> None:
        self.posts = posts
        self.fail = fail

    def __enter__(self) -> _Http:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def close(self) -> None:
        return None

    def get(self, _url: str) -> Any:
        import httpx

        raise httpx.ConnectError("vault down")

    def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        import httpx

        self.posts.append({"url": url, "json": json or {}, "headers": headers or {}})
        if self.fail:
            raise httpx.ConnectError("vault down")
        return _ChatResp()


class _ChatResp:
    status_code = 200

    def json(self) -> dict[str, Any]:
        body = {
            "question": "which-qx-measure-881",
            "options": [
                {
                    "id": "opt_a",
                    "label": "use-qxalpha771",
                    "binding": {
                        "kind": "measure",
                        "name": "qxalpha771",
                        "table": "qxalpha_fact",
                    },
                },
                {
                    "id": "opt_b",
                    "label": "use-qxbeta771",
                    "binding": {
                        "kind": "measure",
                        "name": "qxbeta771",
                        "table": "qxbeta_fact",
                    },
                },
            ],
        }
        return _completion(json.dumps(body))


def _install_http(monkeypatch: pytest.MonkeyPatch, *, fail: bool) -> list[dict[str, Any]]:
    import httpx

    posts: list[dict[str, Any]] = []

    def _client(*_args: object, **_kwargs: object) -> _Http:
        return _Http(posts, fail=fail)

    monkeypatch.setattr(httpx, "Client", _client)
    return posts


def test_wiring_writer_clarifies(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor
    from dms_api.wiring import build_ask_service

    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    posts = _install_http(monkeypatch, fail=False)
    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    cortex = _Cortex()
    try:
        exe = build_ask_service(
            cortex,  # type: ignore[arg-type]
            openvault_url="http://ov.test",
            warehouse_path=wh,
            session_store=DemoSessionStore(
                extra_grants=_GRANTED,
                uploads=lambda: (),
                warehouse=wh,
            ),
        )
    finally:
        dms_executor.probe_openvault = previous
    try:
        env = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_wire",
            tables=["qxalpha_fact", "qxbeta_fact"],
        )
        assert env["status"] == "clarify"
        assert env["rows"] == []
        assert cortex.submits == []
        assert cortex.asks == []
        chat = [p for p in posts if str(p["url"]).endswith("/v1/chat/completions")]
        assert len(chat) == 1
        body = chat[0]["json"]
        assert body["model_preference"] == "free+normal"
        assert "api_key" not in body
        assert "sk-" not in json.dumps(body)
        writer_model = str(body["model"])
        assert writer_model
        import sys

        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from clarify_pick import SameModelRefused, pick_clarify

        def _same(_question: str, _options: list[dict[str, str]]) -> str:
            raise AssertionError("pick model must not run when it is the writer")

        try:
            pick_clarify(
                env,
                _same,
                writer_model_id=writer_model,
                pick_model_id=writer_model,
            )
            raise AssertionError("same model id must be refused")
        except SameModelRefused:
            pass
        seen: list[list[dict[str, str]]] = []

        def _other(_question: str, options: list[dict[str, str]]) -> str:
            seen.append(options)
            return "opt_a"

        got = pick_clarify(
            env,
            _other,
            writer_model_id=writer_model,
            pick_model_id=writer_model + "-pick",
        )
        assert got == {"outcome": "pick", "option_id": "opt_a"}
        assert seen
        assert "binding" not in json.dumps(seen)
        assert_envelope_valid(env)
    finally:
        exe.close()


def test_wiring_writer_down_continues(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One OpenVault attempt, then the normal pipeline. Fails on bd87a4bf."""
    import dms_executor
    from dms_api.wiring import build_ask_service

    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    posts = _install_http(monkeypatch, fail=True)
    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    cortex = _Cortex()
    try:
        exe = build_ask_service(
            cortex,  # type: ignore[arg-type]
            openvault_url="http://ov.test",
            warehouse_path=wh,
            session_store=DemoSessionStore(
                extra_grants=_GRANTED,
                uploads=lambda: (),
                warehouse=wh,
            ),
        )
    finally:
        dms_executor.probe_openvault = previous
    exe._minter = _minter()
    reset_extra_model_calls()
    try:
        env = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_wire_down",
            tables=["qxalpha_fact", "qxbeta_fact"],
        )
        chat = [p for p in posts if str(p["url"]).endswith("/v1/chat/completions")]
        assert len(chat) == 1
        assert extra_model_calls() == 1
        assert env.get("status") != "clarify"
        assert env.get("clarify_skipped") == "writer_unavailable"
        assert env.get("abstain_reason") != "clarify_writer_unavailable"
        if env.get("abstained"):
            assert env.get("abstain_reason")
            assert "clarify_writer" not in str(env.get("abstain_reason"))
        else:
            assert env["rows"]
            assert "Reading used:" in str(env.get("text") or "")
        assert_envelope_valid(env)
    finally:
        exe.close()


def test_wiring_clear_ask_does_not_post(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor
    from dms_api.wiring import build_ask_service

    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    posts = _install_http(monkeypatch, fail=False)
    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    cortex = _Cortex()
    try:
        exe = build_ask_service(
            cortex,  # type: ignore[arg-type]
            openvault_url="http://ov.test",
            warehouse_path=wh,
            session_store=DemoSessionStore(
                extra_grants=_GRANTED,
                uploads=lambda: (),
                warehouse=wh,
            ),
        )
    finally:
        dms_executor.probe_openvault = previous
    exe._minter = _minter()
    try:
        env = exe.live_ask(
            "show qxdelta771",
            space_id=FINANCE,
            session_id="ses_wire_clear",
            tables=["qxdelta_fact"],
        )
        chat = [p for p in posts if str(p["url"]).endswith("/v1/chat/completions")]
        assert chat == []
        assert env.get("status") != "clarify"
        assert env["rows"] == [{"n": 4}]
    finally:
        exe.close()


def test_live_scorers_leave_unanswered_clarify_unanswered() -> None:
    import sys

    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from bird_minidev import grade_envelope
    from score_answers import judge as hostile_judge
    from score_bird import _path_report, _tally
    from score_curated import judge as curated_judge
    from score_curated import without_replaced_clarifies

    clarify = {
        "status": "clarify",
        "clarify_id": "clr_parent",
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "question": "which-qx-measure-881",
    }
    none_fits = {
        "outcome": "miss",
        "option_id": "none_fits",
        "badge": "L0_CERTIFIED",
        "abstained": False,
        "rows": [{"n": 4}],
    }
    reask_abs = {
        "clarify_reask": True,
        "clarify_parent_id": "clr_parent",
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "abstain_reason": "ungranted_table:qxhidden_fact",
    }
    reask_ok = {
        "clarify_reask": True,
        "clarify_parent_id": "clr_parent",
        "badge": "L0_CERTIFIED",
        "abstained": False,
        "rows": [{"n": 4}],
    }
    case = {"expect": "l0", "min_rows": 1}
    assert hostile_judge(clarify, [("n", 4.0)])[0] == "clarify"
    assert hostile_judge(none_fits, [("n", 4.0)])[0] == "clarify"
    assert hostile_judge(reask_abs, [("n", 4.0)])[0] == "abstained"
    assert curated_judge(case, clarify) == "CLARIFY"
    assert curated_judge(case, none_fits) == "CLARIFY"
    assert curated_judge(case, reask_abs) == "ABSTAIN"
    assert grade_envelope(none_fits, [{"n": 4}]) == "CLARIFY"
    assert grade_envelope(clarify, []) == "CLARIFY"
    bird = _tally()
    for env in (clarify, none_fits, reask_abs):
        verdict = curated_judge({"expect": "answered", "min_rows": 1}, env)
        bird[verdict] += 1
    report = _path_report("generative_live", bird, 3)
    assert report["answered"] == 0
    folded = without_replaced_clarifies([clarify, reask_ok])
    assert folded == [reask_ok]
    assert curated_judge(case, folded[0]) in {"OK", "LAYER"}


def test_ten_thousand_minted_ids_are_unchanged_by_the_masker() -> None:
    """Real masker, 10_000 fresh ids. A hex id still changes, so this goes red
    if minting is swapped back to ``clr_`` plus digits.
    """
    from dms_core.pii import mask_unknown_keys
    from dms_executor.ask_clarify import mint_clarify_id

    hex_id = "clr_8812403473c94ce2"
    hex_out = mask_unknown_keys({"clarify_id": hex_id})["clarify_id"]
    assert hex_out != hex_id
    assert "DMSMASK" in str(hex_out)
    for _ in range(10_000):
        cid = mint_clarify_id()
        assert cid.startswith("clr") and cid.isalpha()
        assert "@" not in cid and "_" not in cid
        out = mask_unknown_keys({"clarify_id": cid, "question": "show qx"})
        assert out["clarify_id"] == cid


def test_thousand_ids_round_trip_from_envelope_to_pick(wh: Path) -> None:
    from dms_core.pii import mask_unknown_keys
    from dms_executor.ask_clarify import mint_clarify_id, resolve_clarify

    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
    )
    try:
        attempt = exe._clarify_attempts[env["clarify_id"]]
        live_id = ""
        for n in range(1000):
            cid = mint_clarify_id()
            outgoing = mask_unknown_keys({**env, "clarify_id": cid})
            assert outgoing["clarify_id"] == cid
            exe._clarify_attempts[cid] = {**attempt, "clarify_id": cid}
            resolved = resolve_clarify(
                exe._clarify_attempts,
                clarify_id=str(outgoing["clarify_id"]),
                option_id="opt_a",
                clarify_text=None,
                space_id=FINANCE,
                session_id="ses_clarify",
                fallback_question="show qxalpha771 and qxbeta771",
                warehouse=wh,
                grantable=set(_GRANTED),
            )
            assert isinstance(resolved, str)
            assert "qxalpha771" in resolved
            if n == 0:
                live_id = cid
                exe._clarify_attempts[cid] = {**attempt, "clarify_id": cid}
        picked = exe.live_ask(
            "show qxalpha771 and qxbeta771",
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=["qxalpha_fact", "qxbeta_fact"],
            clarify_id=live_id,
            option_id="opt_a",
        )
        assert picked.get("status") != "clarify"
        assert picked["rows"] == [{"n": 4}]
        assert not picked.get("abstain_reason")
    finally:
        exe.close()


def test_clarify_question_and_label_pii_stay_masked(wh: Path) -> None:
    phone = "202-555-0147"
    email = "ada@example.com"
    payload = {
        "question": f"which measure {phone} {email}",
        "prompt_tokens": 6,
        "completion_tokens": 4,
        "options": [
            _opt("opt_a", f"alpha {phone} {email}", _measure("qxalpha771", "qxalpha_fact")),
            _opt("opt_b", f"beta {phone} {email}", _measure("qxbeta771", "qxbeta_fact")),
        ],
    }
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(payload),
        flag=True,
    )
    try:
        blob = json.dumps(env)
        assert phone not in blob
        assert email not in blob
        assert "DMSMASK_phone_" in blob
        assert "DMSMASK_email_" in blob
        assert_envelope_valid(env)
    finally:
        exe.close()


def test_phone_or_nric_id_is_masked_and_the_pick_abstains(wh: Path) -> None:
    from dms_core.pii import mask_unknown_keys

    phone = "2025550147"
    nric = "900101011234"
    cortex = _Cortex()
    env, _held, exe = _ask(
        wh,
        "show qxalpha771 and qxbeta771",
        ["qxalpha_fact", "qxbeta_fact"],
        writer=_Writer(_two_measures()),
        flag=True,
        cortex=cortex,
    )
    try:
        attempt = exe._clarify_attempts.pop(env["clarify_id"])
        for bad in (phone, nric):
            outgoing = mask_unknown_keys({**env, "clarify_id": bad})
            assert outgoing["clarify_id"] != bad
            assert "DMSMASK" in str(outgoing["clarify_id"])
            exe._clarify_attempts[bad] = {**attempt, "clarify_id": bad}
            for sent in (bad, str(outgoing["clarify_id"])):
                picked = exe.live_ask(
                    "show qxalpha771 and qxbeta771",
                    space_id=FINANCE,
                    session_id="ses_clarify",
                    tables=["qxalpha_fact", "qxbeta_fact"],
                    clarify_id=sent,
                    option_id="opt_a",
                )
                assert picked["abstain_reason"] == "clarify_unknown"
                assert picked["abstained"] is True
                assert picked["rows"] == []
            assert bad in exe._clarify_attempts
        assert cortex.submits == []
        assert cortex.asks == []
    finally:
        exe.close()


def _ticket_rows(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    import logging

    rows: list[dict[str, Any]] = []
    for rec in caplog.records:
        if rec.name != "dms_executor.pipeline_failure":
            continue
        text = rec.getMessage()
        if not text.startswith("pipeline_failure "):
            continue
        assert rec.levelno == logging.WARNING, rec.levelname
        got = json.loads(text[len("pipeline_failure ") :])
        assert isinstance(got, dict)
        rows.append(got)
    return rows


def _one_ticket(
    caplog: pytest.LogCaptureFixture,
    *,
    reason: str,
    question: str,
    hidden: tuple[str, ...],
) -> dict[str, Any]:
    import hashlib

    import dms_executor.pipeline_failure as tickets

    rows = _ticket_rows(caplog)
    assert len(rows) == 1, rows
    payload = rows[0]
    assert payload["reason"] == reason
    assert payload["stage"] == "clarify"
    assert payload["count"] == 1
    assert payload["retries"] == 0
    assert "sql" not in payload
    assert "question" not in payload
    assert "mask_failed" not in payload
    masked, failed = tickets._masked(question)
    assert failed is False
    digest = hashlib.sha256(tickets._normalise(masked or "").encode()).hexdigest()
    assert payload["question_hash"] == digest
    blob = "\n".join(
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "dms_executor.pipeline_failure"
    )
    for item in hidden:
        assert item not in blob, item
    ticket_id = str(payload["ticket_id"])
    assert ticket_id.startswith("tkt") and ticket_id.isalpha()
    return payload


def _arm_tickets(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    import dms_executor.pipeline_failure as tickets

    monkeypatch.setenv("DMS_CLOOP_B", "1")
    tickets._reset_pipeline_failures()
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    caplog.clear()


class _Down:
    def __call__(self, prompt: str) -> dict[str, Any]:
        from dms_executor.ask_clarify import ClarifyWriterUnavailable

        raise ClarifyWriterUnavailable("down")


def test_one_ticket_per_clarify_exit(
    wh: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Flag on, real masker. One WARNING ticket per exit, no question or SQL text."""
    import dms_executor.ask_clarify as ask_clarify

    source = Path(ask_clarify.__file__ or "").read_text(encoding="utf-8")
    assert "log_pipeline_failure_ticket" not in source
    assert "except: pass" not in source
    question = "show qxalpha771 and qxbeta771 call 202-555-0147"
    hidden = (question, "202-555-0147", "SELECT 4", "SELECT")
    tables = ["qxalpha_fact", "qxbeta_fact"]

    _arm_tickets(caplog, monkeypatch)
    env, cortex, exe = _ask(wh, question, tables, writer=_Writer(_two_measures()), flag=True)
    try:
        cid = env["clarify_id"]
        exe._clarify_attempts[cid]["options"][0]["binding"] = {
            "kind": "table",
            "name": "qxhidden_fact",
        }
        _arm_tickets(caplog, monkeypatch)
        picked = exe.live_ask(
            question,
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=tables,
            clarify_id=cid,
            option_id="opt_a",
        )
        assert picked["abstain_reason"] == "clarify_binding_ungranted"
        _one_ticket(caplog, reason="clarify_binding_ungranted", question=question, hidden=hidden)
        assert cortex.submits == []
    finally:
        exe.close()

    _arm_tickets(caplog, monkeypatch)
    env, _held, exe = _ask(wh, question, tables, writer=_Writer(_two_measures()), flag=True)
    try:
        cid = env["clarify_id"]
        exe._clarify_attempts[cid]["created"] = 0
        _arm_tickets(caplog, monkeypatch)
        picked = exe.live_ask(
            question,
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=tables,
            clarify_id=cid,
            option_id="opt_a",
        )
        assert picked["abstain_reason"] == "clarify_expired"
        _one_ticket(caplog, reason="clarify_expired", question=question, hidden=hidden)
    finally:
        exe.close()

    _arm_tickets(caplog, monkeypatch)
    env, cortex, exe = _ask(
        wh, question, tables, writer=_Writer(_two_measures()), flag=True, cortex=_Cortex()
    )
    try:
        _arm_tickets(caplog, monkeypatch)
        picked = exe.live_ask(
            question,
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=tables,
            clarify_id=env["clarify_id"],
            option_id="none_fits",
        )
        assert picked["abstain_reason"] == "none_fits"
        assert picked["rows"] == []
        assert cortex.submits == []
        assert cortex.asks == []
        _one_ticket(caplog, reason="none_fits", question=question, hidden=hidden)
    finally:
        exe.close()

    reask = "use qxhidden_fact ungranted-token-552"
    _arm_tickets(caplog, monkeypatch)
    env, cortex, exe = _ask(
        wh, question, tables, writer=_Writer(_two_measures()), flag=True, cortex=_Cortex()
    )
    try:
        _arm_tickets(caplog, monkeypatch)
        picked = exe.live_ask(
            question,
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=tables,
            clarify_id=env["clarify_id"],
            clarify_text=reask,
        )
        assert picked["abstain_reason"] == "ungranted_table"
        assert "qxhidden_fact" not in json.dumps(picked)
        assert cortex.submits == []
        _one_ticket(
            caplog,
            reason="ungranted_table",
            question=reask,
            hidden=hidden + (reask, "qxhidden_fact", "ungranted-token-552"),
        )
    finally:
        exe.close()

    _arm_tickets(caplog, monkeypatch)
    env, _held, exe = _ask(wh, question, tables, writer=_Down(), flag=True)
    try:
        assert env.get("clarify_skipped") == "writer_unavailable"
        assert env.get("abstain_reason") != "clarify_writer_unavailable"
        assert env.get("status") != "clarify"
        assert env.get("ticket_id") is None
        if env.get("abstained"):
            raise AssertionError(env.get("abstain_reason"))
        assert "Reading used:" in str(env.get("text") or "")
        _one_ticket(caplog, reason="writer_unavailable", question=question, hidden=hidden)
    finally:
        exe.close()


def test_flag_off_writes_no_clarify_ticket(
    wh: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Clarify flag off: the same asks write zero tickets, even with the loop flag on."""
    question = "show qxalpha771 and qxbeta771 call 202-555-0147"
    tables = ["qxalpha_fact", "qxbeta_fact"]
    _arm_tickets(caplog, monkeypatch)
    env, cortex, exe = _ask(wh, question, tables, writer=_Writer(_two_measures()), flag=False)
    try:
        assert env.get("status") != "clarify"
        assert env.get("clarify_skipped") is None
        assert env.get("ticket_id") is None
        assert "clarify_id" not in env
        picked = exe.live_ask(
            question,
            space_id=FINANCE,
            session_id="ses_clarify",
            tables=tables,
            clarify_id="clraaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            option_id="none_fits",
            clarify_text="use qxhidden_fact ungranted-token-552",
        )
        assert picked.get("abstain_reason") != "none_fits"
        assert picked.get("clarify_skipped") is None
        assert picked.get("ticket_id") is None
        assert _ticket_rows(caplog) == []
        assert cortex.asks
    finally:
        exe.close()
