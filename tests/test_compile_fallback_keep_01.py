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


def test_deadline_serves_the_compile_inside_the_budget(
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
    assert cortex.calls == 2
    assert elapsed < deadline
    assert cortex.running is False
    assert cortex.sleep_finished is False
    _assert_compile(env)
    # The slow call was cancelled. It did not take a second cap slot.
    assert env.get("model_calls") == 1
    counted = [item for item in env["loop"] if item.get("model_calls") == 1]
    discarded = [item for item in env["loop"] if item.get("model_calls") == 0]
    assert len(counted) == 1
    assert len(discarded) == 1
    assert discarded[0]["outcome"] == "insights_timeout:generate"


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
