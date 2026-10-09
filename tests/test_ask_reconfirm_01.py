"""ASK-RECONFIRM-01. Synthetic schema, stubbed model. No founder questions.

Direct refusals stay abstains: ungranted, person/PII, destructive/write,
and sql_dialect_unknown. Capacity stops confirm and Yes reruns the pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dms_executor.pipeline_failure as tickets
import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.ask_clarify import minted_clarify_id
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

_MP: pytest.MonkeyPatch | None = None

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
CLOCK = "2026-10-08T00:00:00Z"
Q_STUCK = "What is the qx marker figure?"
Q_NARROW = "Show the qx marker figure"
Q_DECOY = "client supplied text that must be ignored"
SQL_OK = "SELECT 1 AS n"
SQL_BAD = "SELECT nope FROM inventory"
PASSPORT_VALUE = "QXPASS441771"
TEXT_VALUE = "qxtextsample441"
AMOUNT_SAMPLE = "441771"
DEADLINE = "This took too long on our side, so I stopped before answering."
LEASE = "We hit our serving limit, so I stopped before answering."
NOT_FOUND = "Not found in the database."
SUGGESTION_52 = "Show the marker figure"
SQL_52 = "SELECT 1 AS n"

_GRANTED = (
    "qxalpha_fact",
    "qxpii_fact",
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
    db: Path
    script: list[dict[str, Any]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.questions.append(question)
        if not self.script:
            return {"unsure": True}
        return dict(self.script.pop(0))

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_file(self.db)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_exec", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="led_reconfirm", hash="hash_reconfirm")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="stub contract answer",
            badge="certified",
            sql_used="SELECT 4",
            rows=[{"n": 4}],
            assumptions="fixture",
            audit_id="aud_reconfirm",
            route="sql",
        )


class _Writer:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = {} if payload is None else payload
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
        con.execute(
            "CREATE TABLE qxpii_fact (passport_no VARCHAR, amount DOUBLE, notes VARCHAR)"
        )
        con.execute(
            "INSERT INTO qxpii_fact VALUES (?, ?, ?)",
            [PASSPORT_VALUE, float(AMOUNT_SAMPLE), TEXT_VALUE],
        )
        con.execute("ALTER TABLE inventory ADD COLUMN passport_no VARCHAR")
        con.execute("UPDATE inventory SET passport_no = ?", [PASSPORT_VALUE])
    finally:
        con.close()


def _add_secret(path: Path, secret: str) -> None:
    con = connect_file(path)
    try:
        con.execute(f"CREATE TABLE {secret} (secret_amount DOUBLE)")
        con.execute(f"INSERT INTO {secret} VALUES (9)")
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


def _rows(path: Path, sql: str) -> list[dict[str, Any]]:
    con = connect_file(path)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _same_rows(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )


@pytest.fixture
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    global _MP
    _MP = monkeypatch
    monkeypatch.delenv("DMS_ASK_RECONFIRM", raising=False)
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.setattr("dms_executor.envelope._now", lambda: CLOCK)
    path = tmp_path / "reconfirm.duckdb"
    _seed(path)
    tickets._reset_pipeline_failures()
    return path


def _flag(on: bool) -> None:
    assert _MP is not None
    if on:
        _MP.setenv("DMS_ASK_RECONFIRM", "1")
    else:
        _MP.delenv("DMS_ASK_RECONFIRM", raising=False)


def _ask(
    path: Path,
    question: str,
    *,
    writer: _Writer | None,
    flag: bool,
    script: list[dict[str, Any]],
    confirm_id: str | None = None,
    confirm_choice: str | None = None,
    cortex: _Cortex | None = None,
    exe: Executor | None = None,
) -> tuple[dict[str, Any], _Cortex, Executor]:
    _flag(flag)
    held = cortex or _Cortex(db=path, script=list(script))
    owned = exe or _executor(path, writer, held)
    env = owned.live_ask(
        question,
        space_id=FINANCE,
        session_id="ses_reconfirm",
        confirm_id=confirm_id,
        confirm_choice=confirm_choice,
    )
    return env, held, owned


def _blob(env: dict[str, Any]) -> str:
    return json.dumps(env, default=str)


def _records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in caplog.records:
        if rec.name != "dms_executor.pipeline_failure":
            continue
        text = rec.getMessage()
        if not text.startswith("pipeline_failure "):
            continue
        assert rec.levelno == logging.WARNING
        got = json.loads(text[len("pipeline_failure ") :])
        assert isinstance(got, dict)
        out.append(got)
    return out


def test_confirm_then_yes_matches_executed_rows(wh: Path) -> None:
    writer = _Writer({"reason": "I could not total that column.", "suggested_question": Q_NARROW})
    first, cortex, exe = _ask(
        wh,
        Q_STUCK,
        writer=writer,
        flag=True,
        script=[{"unsure": True}],
    )
    try:
        assert first["status"] == "confirm"
        assert first["abstained"] is True
        assert first["rows"] == []
        assert first["confirm_reason"] == "I could not total that column."
        assert first["suggested_question"] == Q_NARROW
        assert minted_clarify_id(first["confirm_id"])
        assert writer.prompts
        prompt = writer.prompts[0]
        assert PASSPORT_VALUE not in prompt
        assert TEXT_VALUE not in prompt
        assert "passport_no" in prompt
        assert_envelope_valid(first)
        cortex.script.append({"query_sql": SQL_OK})
        yes, _, _ = _ask(
            wh,
            Q_DECOY,
            writer=writer,
            flag=True,
            script=[],
            confirm_id=first["confirm_id"],
            confirm_choice="yes",
            cortex=cortex,
            exe=exe,
        )
        assert Q_DECOY not in cortex.questions
        assert cortex.questions[-1] == Q_NARROW
        assert yes["abstained"] is False
        assert yes["badge"] == "L2_VALIDATED"
        assert yes.get("status") != "confirm"
        assert _same_rows(yes["rows"], _rows(wh, SQL_OK))
        assert yes["rows"]
        assert cortex.asks == []
        assert_envelope_valid(yes)
    finally:
        exe.close()


@pytest.mark.parametrize(
    ("code", "plain"),
    [
        ("serving_deadline_exceeded", DEADLINE),
        ("serving_lease_cap", LEASE),
    ],
)
def test_capacity_confirms_and_yes_reruns(wh: Path, code: str, plain: str) -> None:
    writer = _Writer({})
    first, cortex, exe = _ask(
        wh,
        Q_STUCK,
        writer=writer,
        flag=True,
        script=[{"insights_fail": code}],
    )
    try:
        assert first["status"] == "confirm"
        assert first["confirm_code"] == code
        assert first["confirm_reason"] == plain
        assert first["suggested_question"] == Q_STUCK
        assert minted_clarify_id(first["confirm_id"])
        assert "suggested_question" in first
        assert_envelope_valid(first)
        cortex.script.append({"query_sql": SQL_OK})
        yes, _, _ = _ask(
            wh,
            Q_DECOY,
            writer=writer,
            flag=True,
            script=[],
            confirm_id=first["confirm_id"],
            confirm_choice="yes",
            cortex=cortex,
            exe=exe,
        )
        assert Q_DECOY not in cortex.questions
        assert cortex.questions[-1] == Q_STUCK
        assert yes["abstained"] is False
        assert _same_rows(yes["rows"], _rows(wh, SQL_OK))
        assert cortex.asks == []
    finally:
        exe.close()


def test_decline_is_not_found_with_one_ticket(wh: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _MP is not None
    _MP.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.INFO)
    writer = _Writer({"reason": "I could not total that column.", "suggested_question": Q_NARROW})
    first, cortex, exe = _ask(
        wh,
        Q_STUCK,
        writer=writer,
        flag=True,
        script=[{"unsure": True}],
    )
    try:
        tickets._reset_pipeline_failures()
        caplog.clear()
        no, _, _ = _ask(
            wh,
            Q_DECOY,
            writer=writer,
            flag=True,
            script=[],
            confirm_id=first["confirm_id"],
            confirm_choice="no",
            cortex=cortex,
            exe=exe,
        )
        assert no["abstained"] is True
        assert no["text"] == NOT_FOUND
        assert no["abstain_reason"] == "not_found"
        assert no.get("status") != "confirm"
        assert no["rows"] == []
        assert_envelope_valid(no)
        rows = _records(caplog)
        assert len(rows) == 1
        assert rows[0]["reason"] == "not_found"
        assert rows[0]["stage"] == "reconfirm"
        blob = json.dumps(rows[0])
        assert Q_STUCK not in blob
        assert SQL_OK not in blob
    finally:
        exe.close()


def test_yes_checker_failure_serves_no_rows(wh: Path) -> None:
    assert _MP is not None
    _MP.setenv("DMS_CLOOP_B", "1")
    _MP.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    writer = _Writer({"reason": "I could not total that column.", "suggested_question": Q_NARROW})
    first, cortex, exe = _ask(
        wh,
        Q_STUCK,
        writer=writer,
        flag=True,
        script=[{"unsure": True}],
    )
    try:
        cortex.script.extend([{"query_sql": SQL_BAD}, {"query_sql": SQL_BAD}])
        yes, _, _ = _ask(
            wh,
            Q_DECOY,
            writer=writer,
            flag=True,
            script=[],
            confirm_id=first["confirm_id"],
            confirm_choice="yes",
            cortex=cortex,
            exe=exe,
        )
        assert yes["abstained"] is True
        assert yes["rows"] == []
        assert yes["values"] == []
        assert yes.get("sql_used") in (None, "")
        assert yes.get("status") != "confirm"
        assert "999" not in str(yes.get("text") or "")
        assert cortex.asks == []
        assert_envelope_valid(yes)
    finally:
        exe.close()


def test_hard_refusals_have_no_suggestion(wh: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _MP is not None
    writer = _Writer({"reason": "try this", "suggested_question": Q_NARROW})
    secret = "qxzz9f3a2c1b7e4d"
    _add_secret(wh, secret)
    cases = (
        ("ungranted", {"query_sql": f"SELECT 1 AS n FROM {secret}"}, "ungranted"),
        ("pii", {"query_sql": "SELECT passport_no FROM inventory"}, "pii_column"),
        ("write", {"query_sql": "DELETE FROM inventory"}, "hostile_sql"),
    )
    for name, payload, token in cases:
        held = _Cortex(db=wh, script=[payload])
        env, _, exe = _ask(wh, Q_STUCK, writer=writer, flag=True, script=[], cortex=held)
        try:
            assert env.get("status") != "confirm", name
            assert "suggested_question" not in env, name
            assert env["abstained"] is True, name
            assert env["rows"] == [], name
            blob = _blob(env).lower()
            assert token in blob, name
            assert secret not in blob, name
            assert PASSPORT_VALUE.lower() not in blob, name
        finally:
            exe.close()
        assert writer.prompts == [], name

    _MP.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.INFO)
    tickets._reset_pipeline_failures()
    caplog.clear()
    _MP.setattr("dms_executor.sql_loop.extract_dialect", lambda *_a, **_k: "")
    env, _, exe = _ask(wh, Q_STUCK, writer=writer, flag=True, script=[])
    try:
        assert env.get("status") != "confirm"
        assert "suggested_question" not in env
        assert env["abstain_reason"] == "sql_dialect_unknown"
        assert env["abstained"] is True
        rows = _records(caplog)
        assert len(rows) == 1
        assert rows[0]["reason"] == "sql_dialect_unknown"
        assert rows[0]["stage"] == "pipeline"
        assert Q_STUCK not in json.dumps(rows[0])
    finally:
        exe.close()
    assert writer.prompts == []


def test_ungranted_name_absent_from_http_body(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ASK_RECONFIRM", "1")
    secret = "qxzz9f3a2c1b7e4d"
    _add_secret(wh, secret)
    import dms_executor
    from dms_api.app import create_app
    from dms_api.deps import get_settings
    from dms_api.settings import Settings
    from fastapi.testclient import TestClient

    cortex = _Cortex(db=wh, script=[{"query_sql": f"SELECT 1 AS n FROM {secret}"}])
    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    try:
        app = create_app()
    finally:
        dms_executor.probe_openvault = previous
    exe = _executor(wh, _Writer({"reason": "try", "suggested_question": Q_NARROW}), cortex)
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
        res = client.post(
            "/v1/chat/ask",
            json={"question": Q_STUCK, "space_id": FINANCE, "session_id": "ses_http"},
        )
        assert res.status_code == 200
        body = res.text
        folded = body.casefold()
        assert secret.casefold() not in folded
        assert f"main.{secret}".casefold() not in folded
        assert env_status(res) != "confirm"
        parsed = res.json()
        assert "suggested_question" not in parsed
    finally:
        exe.close()


def env_status(res: Any) -> str:
    body = res.json()
    return str(body.get("status") or "")


def test_flag_off_adds_no_confirm_keys(wh: Path) -> None:
    writer = _Writer({"reason": "I could not total that column.", "suggested_question": Q_NARROW})
    env, _, exe = _ask(wh, Q_STUCK, writer=writer, flag=False, script=[{"unsure": True}])
    try:
        assert env.get("status") != "confirm"
        for key in ("confirm_id", "confirm_reason", "suggested_question", "confirm_code"):
            assert key not in env
        assert writer.prompts == []
    finally:
        exe.close()


def test_former_abstains_suggest_and_yes_is_not_wrong(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Counts only. Question text stays out of the assertion messages."""
    from tests.fixtures.ask_guide.capture_flag_off_52 import replay_pack
    from tests.test_one_path_check_01_shadow import GOLDEN, _pin_capture_day

    _pin_capture_day(monkeypatch)
    monkeypatch.setenv("DMS_ASK_RECONFIRM", "1")
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    former = [row["id"] for row in golden if row["env"].get("badge") == "ABSTAIN"]
    yes_db = tmp_path / "yes.duckdb"
    duckdb.connect(str(yes_db)).close()

    class _Always:
        def __call__(self, _prompt: str) -> dict[str, Any]:
            return {
                "reason": "I could not answer that from the data this Space can read.",
                "suggested_question": SUGGESTION_52,
            }

    rows = replay_pack(
        reconfirm_writer=_Always(),
        suggestion=SUGGESTION_52,
        suggestion_sql=SQL_52,
        follow_yes=True,
        yes_warehouse=yes_db,
    )
    by_id = {row["id"]: row for row in rows}
    suggested = 0
    correct = 0
    wrong = 0
    for qid in former:
        env = by_id[qid]["env"]
        if env.get("status") != "confirm":
            continue
        suggested += 1
        yes = by_id[qid].get("yes")
        assert isinstance(yes, dict), qid
        if yes.get("abstained") or yes.get("badge") == "ABSTAIN":
            continue
        sql = str(yes.get("sql_used") or "").strip()
        if not sql:
            wrong += 1
            continue
        try:
            fresh = _rows(yes_db, sql)
        except Exception:
            wrong += 1
            continue
        if _same_rows(yes.get("rows"), fresh):
            correct += 1
        else:
            wrong += 1
    report = {"suggested": suggested, "yes_correct": correct, "wrong": wrong, "former": len(former)}
    Path("/tmp/ask_reconfirm_counts.json").write_text(json.dumps(report), encoding="utf-8")
    assert wrong == 0
    assert suggested > 0
