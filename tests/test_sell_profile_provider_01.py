"""Flag-on answers come from a recorded OpenVault tool call, then the DB.

No live key. Stub insights that carry query_sql must not be the source.
The three demo-warehouse questions run the provider SQL and the rows are
the warehouse rows.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.sell_profile import reset_provider_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import grade52  # noqa: E402

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
FIXTURE = ROOT / "tests" / "fixtures" / "sell_profile" / "provider_demo.json"
QUESTIONS = (
    "How much stock is in each warehouse?",
    "Who are the suppliers?",
    "What are the warehouse names?",
)
POISON = "SELECT 999 AS n FROM inventory"


class _Poison:
    """Insights and /ask that would serve a fake figure if the floor used them."""

    def __init__(self, db: Path) -> None:
        self.db = db
        self.insights: list[str] = []
        self.asks: list[Any] = []

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {
            "query_sql": POISON,
            "served_model": "stub-insights",
            "served_provider": "stub",
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        if kind == "session_bind" or not sql.strip():
            return QueryResult(ok=True, status="bound", run_id="bind_demo", output={})
        con = connect_file(self.db)
        try:
            cur = con.execute(sql)
            cols = [str(item[0]) for item in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_demo", output={"rows": rows})

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Spoof total 999999.",
            badge="certified",
            sql_used=POISON,
            rows=[{"n": 999999}],
            audit_id="aud_spoof",
            route="certified_metric",
        )

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="led_ok", hash="hash_ok_not_entry")


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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


def _executed(db: Path, sql: str) -> list[dict[str, Any]]:
    con = connect_file(db)
    try:
        cur = con.execute(sql)
        cols = [str(item[0]) for item in (cur.description or [])]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def test_flag_on_answers_three_demo_questions_from_executed_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELL_PROFILE", "1")
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    monkeypatch.setenv("DMS_SELL_PROVIDER_FIXTURE", str(FIXTURE))
    reset_provider_fixture()
    db = tmp_path / "provider.duckdb"
    ensure_demo_warehouse(db)
    cortex = _Poison(db)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    try:
        for question in QUESTIONS:
            env = exe.live_ask(question, space_id=FINANCE, session_id="ses_provider")
            assert_envelope_valid(env)
            assert env["abstained"] is False
            assert env["sell_profile"] is True
            sql = str(env["sql_used"])
            assert sql != POISON
            assert "999" not in sql
            rows = _executed(db, sql)
            assert env["rows"] == rows
            assert rows
            assert grade52.serve_path(env, sql) == "ai"
            assert "999999" not in str(env.get("text") or "")
    finally:
        exe.close()
    assert cortex.insights == []
    assert cortex.asks == []
