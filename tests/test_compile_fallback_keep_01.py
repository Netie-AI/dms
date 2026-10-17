"""COMPILE-FALLBACK-KEEP-01: an exhausted model lane still serves the ranking compile.

The fake speaks the same compute_insights interface and executes SQL on the
seeded demo extract. It has no key. Flags-on must not score below flags-off.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_curated import judge_detailed, load_oracles  # noqa: E402

_BAD = "SELECT nope FROM inventory"
_COLD_Q = "Which locations are cold storage?"
_RANK = "cq_cold_storage"
_PROVIDER = "stub-provider"
_MODEL = "stub-model"
_PACK = Path(__file__).resolve().parents[0] / "fixtures" / "curated_ceo" / "questions.yaml"
_CORRECT = frozenset({"OK", "LAYER"})


def _flags(monkeypatch: pytest.MonkeyPatch, *, cloop: bool) -> None:
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    for name in (
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_LANE_BRONZE_SHEET",
        "DMS_SERVED_ATTR_DIAG",
        "DMS_CCA_CASCADE",
        "DMS_HARNESS_ASK_PATHS",
    ):
        monkeypatch.delenv(name, raising=False)
    if cloop:
        monkeypatch.setenv("DMS_CLOOP_B", "1")
    else:
        monkeypatch.delenv("DMS_CLOOP_B", raising=False)


class _Cortex:
    """Failing model SQL plus a ranking id. Submit runs the statement."""

    def __init__(
        self,
        db: Path,
        *,
        ranking_for: dict[str, str] | None = None,
        slow_after: int = 0,
        sleep_s: float = 0.0,
    ) -> None:
        self._db = db
        self.ranking_for = ranking_for or {}
        self.slow_after = slow_after
        self.sleep_s = sleep_s
        self.calls = 0
        self.running = False
        self.sleep_finished = False

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.slow_after and self.calls >= self.slow_after:
            self._sleep_until_cancelled()
        metric = self.ranking_for.get(question, _RANK)
        return {
            "phase": "generate",
            "query_sql": _BAD,
            "served_model": _MODEL,
            "served_provider": _PROVIDER,
            "ov_key_id": "ovk-stub",
            "ontology": {"metrics": [{"id": metric}]},
            "generative": {"sql": _BAD, "ok": True, "stamp": {"impl": "stub"}},
        }

    def _sleep_until_cancelled(self) -> None:
        """Block until the attempt is cancelled, or the full sleep ends."""
        import dms_executor.sql_loop as sql_loop

        cancel = getattr(sql_loop, "attempt_cancel_event", None)
        event = cancel() if cancel else None
        self.running = True
        try:
            end = time.monotonic() + self.sleep_s
            while time.monotonic() < end:
                if event is not None and event.is_set():
                    return
                time.sleep(0.02)
            self.sleep_finished = True
        finally:
            self.running = False

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_keep_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_file(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_keep", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_keep", hash="hash_keep")

    def ask(self, req: AskRequest) -> AskResponse:
        del req
        return AskResponse(
            answer="I can't answer this.",
            badge="ABSTAIN",
            abstained=True,
            rows=[],
            audit_id="aud_keep_miss",
            route="abstain",
        )


def _executor(db: Path, cortex: _Cortex) -> Executor:
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
            issued_at="2026-09-26T00:00:00+00:00",
            expires_at="2026-09-26T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return Executor(cortex=cortex, minter=minter, warehouse_path=db)  # type: ignore[arg-type]


def _rows_blob(env: dict[str, Any]) -> str:
    return json.dumps(env.get("rows") or [], default=str)


def _assert_compile(env: dict[str, Any]) -> None:
    assert_envelope_valid(env)
    assert env.get("abstained") is not True
    assert env.get("badge") != "ABSTAIN"
    assert "WH-C" in _rows_blob(env)
    assert env.get("plan_origin") == "ontology_ranking"
    assert env.get("served_provider") != _PROVIDER
    assert env.get("served_model") != _MODEL
    assert "served_provider" not in env
    assert "served_model" not in env
    assert env.get("served_attribution") != "reported"


def test_exhausted_loop_serves_the_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    cortex = _Cortex(db)
    env = _executor(db, cortex).live_ask(
        _COLD_Q, session_id="ses_keep", ask_path="generative"
    )
    assert cortex.calls == 2
    _assert_compile(env)
    assert env.get("model_calls") == 2
    assert env.get("loop")
    assert all(item.get("model_calls") == 1 for item in env["loop"])


def _hold_blob(env: dict[str, Any]) -> str:
    notes = " ".join(str(item) for item in (env.get("assumptions") or []))
    return f"{notes} {env.get('text') or ''}"


def _assert_no_compile(env: dict[str, Any]) -> None:
    """Ladder or reconfirm, and the ranking compile served no rows."""
    assert_envelope_valid(env)
    assert env.get("abstained") is True
    assert env.get("badge") != "L2_VALIDATED"
    assert env.get("rows") in (None, [])
    assert "WH-C" not in _rows_blob(env)
    assert env.get("plan_origin") != "ontology_ranking"
    blob = _hold_blob(env)
    # The ask-level serving deadline may close the ask first on a timeout.
    assert (
        "reconfirm" in blob or "self_correct" in blob or "serving_deadline_exceeded" in blob
    )


def test_deadline_does_not_serve_the_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    deadline = 1.5
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", str(deadline))
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    cortex = _Cortex(db, slow_after=2, sleep_s=5.0)
    started = time.monotonic()
    env = _executor(db, cortex).live_ask(
        _COLD_Q, session_id="ses_keep", ask_path="generative"
    )
    elapsed = time.monotonic() - started
    assert cortex.running is False
    assert cortex.sleep_finished is False
    assert elapsed < deadline + 1.0
    _assert_no_compile(env)
    assert "insights_timeout" in _hold_blob(env) or "serving_deadline_exceeded" in _hold_blob(env)
    # The slow call was cancelled. It was sent, so it is counted.
    assert env.get("model_calls") == 2
    assert cortex.calls == 2


def _pack() -> tuple[dict[str, str], list[dict[str, Any]]]:
    raw = yaml.safe_load(_PACK.read_text(encoding="utf-8"))
    spaces = {str(k): str(v) for k, v in (raw.get("spaces") or {}).items()}
    questions = list(raw.get("questions") or [])
    return spaces, questions


def _score(db: Path, *, cloop: bool, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    _flags(monkeypatch, cloop=cloop)
    spaces, questions = _pack()
    ranking = {
        str(row["question"]): str(row.get("cortex_id") or "zzzz_noise")
        for row in questions
    }
    cortex = _Cortex(db, ranking_for=ranking)
    exe = _executor(db, cortex)
    oracles = load_oracles()
    tally = {"correct": 0, "wrong": 0, "abstain": 0, "other": 0}
    for row in questions:
        space = spaces.get(str(row.get("space") or "finance"), "")
        env = exe.live_ask(
            str(row["question"]),
            space_id=space or None,
            session_id="ses_keep_52",
        )
        verdict = judge_detailed(
            row, env, oracle_db=db, oracles=oracles, as_of="2026-10-09"
        ).verdict
        if verdict in _CORRECT:
            tally["correct"] += 1
        elif verdict == "WRONG":
            tally["wrong"] += 1
        elif verdict == "ABSTAIN":
            tally["abstain"] += 1
        else:
            tally["other"] += 1
    tally["n"] = len(questions)
    return tally


def test_flags_on_keeps_flag_off_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "keep52.duckdb"
    ensure_demo_warehouse(db)
    off = _score(db, cloop=False, monkeypatch=monkeypatch)
    on = _score(db, cloop=True, monkeypatch=monkeypatch)
    assert off["other"] == 0, off
    assert on["other"] == 0, on
    assert off["n"] == on["n"] == 52
    assert on["correct"] >= off["correct"], {"off": off, "on": on}
    assert on["wrong"] <= off["wrong"], {"off": off, "on": on}


def test_compile_provenance_names_the_ranking_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    for cloop in (False, True):
        _flags(monkeypatch, cloop=cloop)
        cortex = _Cortex(db)
        env = _executor(db, cortex).live_ask(
            _COLD_Q, session_id="ses_keep", ask_path="generative"
        )
        _assert_compile(env)
        assert "served_model" not in env
        if cloop:
            assert env.get("model_calls") == 2
            assert env.get("loop")
            assert all(item.get("model_calls") == 1 for item in env["loop"])
        else:
            assert env.get("model_calls") == 0


_GOOD_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_BINDER_SQL = "SELECT nope_col FROM inventory"
_FAN_SQL = (
    "SELECT f.sku AS sku FROM inventory f "
    "JOIN (SELECT sku FROM inventory UNION ALL SELECT sku FROM inventory) p "
    "ON f.sku = p.sku"
)


class _BinderThenServe(_Cortex):
    """First call is a binder failure. The retry returns SQL that executes."""

    def __init__(self, db: Path) -> None:
        super().__init__(db)
        self.feedback: list[Any] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.feedback.append(kwargs.get("sql_feedback"))
        self.calls += 1
        sql = _BINDER_SQL if self.calls == 1 else _GOOD_SQL
        return {
            "phase": "generate",
            "query_sql": sql,
            "served_model": _MODEL,
            "served_provider": _PROVIDER,
            "ov_key_id": "ovk-stub",
            "ontology": {"metrics": [{"id": _RANK}]},
            "generative": {"sql": sql, "ok": True, "stamp": {"impl": "stub"}},
        }


def test_binder_exception_retries_once_then_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    cortex = _BinderThenServe(db)
    env = _executor(db, cortex).live_ask(
        _COLD_Q, session_id="ses_keep", ask_path="generative"
    )
    assert cortex.calls == 2
    fed = cortex.feedback[1] or {}
    assert "Referenced column" in str(fed.get("prompt") or "")
    assert env.get("badge") == "L2_VALIDATED"
    assert "WH-C" in _rows_blob(env)
    assert "Referenced column" not in json.dumps(env)
    assert env.get("model_calls") == 2
    assert all(item.get("model_calls") == 1 for item in env["loop"])


class _Raise(_Cortex):
    """The provider call raises. Ranking on the class is unused."""

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        raise RuntimeError("provider down")


class _Empty(_Cortex):
    """A finished call with ranking and no statement."""

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "phase": "generate",
            "query_sql": "",
            "served_model": _MODEL,
            "served_provider": _PROVIDER,
            "ov_key_id": "ovk-stub",
            "ontology": {"metrics": [{"id": _RANK}]},
            "generative": {"sql": "", "ok": True, "stamp": {"impl": "stub"}},
        }


def test_provider_error_does_not_serve_the_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    env = _executor(db, _Raise(db)).live_ask(
        _COLD_Q, session_id="ses_keep", ask_path="generative"
    )
    _assert_no_compile(env)
    blob = _hold_blob(env)
    assert "provider_error" in blob
    assert "provider down" not in json.dumps(env)


def test_empty_reply_does_not_serve_the_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    env = _executor(db, _Empty(db)).live_ask(
        _COLD_Q, session_id="ses_keep", ask_path="generative"
    )
    _assert_no_compile(env)
    assert "empty_reply" in _hold_blob(env)


def test_compile_lot_fanout_is_never_l2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compile that duplicates lot_of_product keys is reconfirm, never L2."""
    from dms_executor.ontology import NO_SILENT_PAD, CompiledQuery, Coverage, Ontology

    _flags(monkeypatch, cloop=True)

    def _compile(self: Any, *_a: Any, **_k: Any) -> CompiledQuery:
        return CompiledQuery(
            sql=_FAN_SQL,
            measure="sku_count",
            grain="lot",
            group_by=("sku",),
            coverage=Coverage(
                include=("lots",),
                exclude=(NO_SILENT_PAD,),
                unsure=(),
            ),
        )

    monkeypatch.setattr(Ontology, "compile", _compile)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    env = _executor(db, _Cortex(db)).live_ask(
        _COLD_Q, session_id="ses_keep", ask_path="generative"
    )
    assert env.get("badge") != "L2_VALIDATED"
    assert env.get("abstained") is True
    blob = " ".join(str(item) for item in (env.get("assumptions") or []))
    blob = f"{blob} {env.get('text') or ''}"
    assert "fanout_subject_keys" in blob
    assert_envelope_valid(env)


# -- supersede #433: one refusal for every rung, cap, first-call bound, no echo --


def _chat_ask(
    db: Path, cortex: _Cortex, question: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """POST /v1/chat/ask on the real route. The envelope is the assertion surface."""
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    settings_mod.get_settings.cache_clear()
    try:
        app = create_app()
        app.state.ask_service = _executor(db, cortex)
        app.state.cortex = cortex
        res = TestClient(app).post(
            "/v1/chat/ask",
            json={
                "question": question,
                "session_id": "ses_keep_http",
                "ask_path": "generative",
            },
        )
    finally:
        settings_mod.get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    assert_envelope_valid(body)
    return body


class _FailThenSql(_Cortex):
    """Call 1 raises (provider error). Every later call returns ``sql``."""

    def __init__(self, db: Path, sql: str) -> None:
        super().__init__(db)
        self.sql = sql
        self.submitted: list[str] = []

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("provider down")
        return {
            "phase": "generate",
            "query_sql": self.sql,
            "served_model": _MODEL,
            "served_provider": _PROVIDER,
            "ov_key_id": "ovk-stub",
            "ontology": {"metrics": [{"id": _RANK}]},
            "generative": {"sql": self.sql, "ok": True, "stamp": {"impl": "stub"}},
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "session_bind":
            body = getattr(req, "body", None)
            self.submitted.append(str(body.get("sql") or "") if isinstance(body, dict) else "")
        return super().submit(req)


def _location_rows(db: Path) -> int:
    con = connect_file(db)
    try:
        return int(con.execute("SELECT count(*) FROM locations").fetchone()[0])
    finally:
        con.close()


@pytest.mark.parametrize(
    ("bad_sql", "refusal"),
    [
        ("SELECT location_code FROM locations; TRUNCATE locations", "multi_statement"),
        (
            "SELECT location_code FROM locations; ALTER TABLE locations RENAME TO zz",
            "multi_statement",
        ),
        ("TRUNCATE locations", "hostile_sql"),
        ("SELECT location_code FROM locations; SELECT 1", "multi_statement"),
    ],
)
def test_ladder_rung_refuses_multi_statement_and_destructive_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_sql: str, refusal: str
) -> None:
    _flags(monkeypatch, cloop=True)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    before = _location_rows(db)
    cortex = _FailThenSql(db, bad_sql)
    env = _chat_ask(db, cortex, _COLD_Q, monkeypatch)
    assert cortex.submitted == []
    assert env.get("abstained") is True
    # Refused at once on the checker's reason, not by a later failure.
    assert f"checker:{refusal}" in _hold_blob(env)
    assert cortex.calls == 2
    assert env.get("badge") not in ("L2_VALIDATED", "L1_CERTIFIED")
    assert env.get("rows") in (None, [])
    assert "WH-C" not in _rows_blob(env)
    assert _location_rows(db) == before
    con = connect_file(db)
    try:
        names = {str(r[0]) for r in con.execute("SHOW TABLES").fetchall()}
    finally:
        con.close()
    assert "zz" not in names and "locations" in names


class _OneFastThenHang(_Cortex):
    """Call 1 returns no statement. Every later call hangs past its slice."""

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any] | None:
        self.calls += 1
        if self.calls == 1:
            return {"phase": "generate", "query_sql": "", "ontology": {"metrics": []}}
        time.sleep(30.0)
        return None


def test_timed_out_calls_count_against_the_cap_and_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "3")
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", "2")
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    cortex = _OneFastThenHang(db)
    env = _chat_ask(db, cortex, _COLD_Q, monkeypatch)
    assert env.get("abstained") is True
    assert cortex.calls == 2
    assert env.get("model_calls") == 2
    assert "WH-C" not in _rows_blob(env)


class _SlowGood(_Cortex):
    """One reply after ``sleep_s`` that executes. Not cancellable."""

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        time.sleep(self.sleep_s)
        return {
            "phase": "generate",
            "query_sql": _GOOD_SQL,
            "served_model": _MODEL,
            "served_provider": _PROVIDER,
            "ov_key_id": "ovk-stub",
            "ontology": {"metrics": [{"id": _RANK}]},
            "generative": {"sql": _GOOD_SQL, "ok": True, "stamp": {"impl": "stub"}},
        }


def test_first_call_keeps_the_per_leg_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    # Bound 5.4 s, cap 2: a split would give the first call 1.8 s, less than
    # the 2.0 s reply. The rest of the ask keeps 3.4 s, so a slow box does not
    # cut the checks, probe and submit that follow the reply.
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "5.4")
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    cortex = _SlowGood(db, sleep_s=2.0)
    env = _chat_ask(db, cortex, _COLD_Q, monkeypatch)
    assert cortex.calls == 1
    assert env.get("abstained") is not True
    assert env.get("badge") == "L2_VALIDATED"
    assert "WH-C" in _rows_blob(env)
    assert env.get("model_calls") == 1


class _EchoFail(_Cortex):
    """A payload whose insights_fail carries a name the grant never gave out."""

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "phase": "generate",
            "insights_fail": "insights_timeout:secret_payroll_table",
            "ontology": {"metrics": [{"id": _RANK}]},
        }


def test_insights_fail_text_is_not_reflected_into_the_abstain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags(monkeypatch, cloop=True)
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    env = _chat_ask(db, _EchoFail(db), _COLD_Q, monkeypatch)
    assert env.get("abstained") is True
    assert "WH-C" not in _rows_blob(env)
    assert "secret_payroll_table" not in json.dumps(env)
    assert "insights_timeout" in _hold_blob(env)
