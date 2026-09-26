"""ORACLE-FIX-02 / dms#308: cq_audit_overdue judged at the answer engine's date.

Seeded tmp lake only. No network, no keys. Expected rows are computed in Python
from the seed's suppliers rows and the certified 90-day rule, never from an
answer. The oracle's CURRENT_DATE is bound to the date the engine read on the
answer's own submit (envelope ``engine_clock``), never the harness clock.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client import LedgerAppendResponse
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.engine_clock import PROBE_SQL, EngineClock, sql_reads_clock
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.verified_queries import register_verified_query
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oracle_row_match import run_oracle_select  # noqa: E402
from score_curated import (  # noqa: E402
    REASON_ENGINE_DATE_CROSSED,
    REASON_ENGINE_DATE_MISSING,
    bind_oracle_as_of,
    judge_envelope_detailed,
    load_oracles,
    load_pack,
    pack_category_report,
    resolve_space,
)

QID = "cq_audit_overdue"
QUESTION = "Which suppliers have an audit overdue?"
# Certified window (Cortex packs/dms/semantic/certified_queries.yaml and
# dms_executor.ontology audit_overdue): last_audit_date < as_of - 90 days.
WINDOW = timedelta(days=90)


def _seed(tmp_path: Path) -> Path:
    from dms_executor import demo_warehouse as dw

    db = tmp_path / "oracle_fix_02.duckdb"
    dw._SEEDED.discard(str(db.resolve()))
    dw.ensure_demo_warehouse(db)
    return db


def _seed_suppliers(db: Path) -> list[tuple[str, date]]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return [
            (str(sid), d)
            for sid, d in con.execute(
                "SELECT supplier_id, last_audit_date FROM suppliers"
            ).fetchall()
        ]
    finally:
        con.close()


def _expected(db: Path, as_of: str) -> set[str]:
    cutoff = date.fromisoformat(as_of) - WINDOW
    return {sid for sid, d in _seed_suppliers(db) if d is not None and d < cutoff}


def _oracle_sql() -> str:
    return str(load_oracles()[QID]["sql"]).strip()


def _case() -> dict[str, Any]:
    for case in load_pack(ROOT / "tests/fixtures/curated_ceo/questions.yaml")["questions"]:
        if case["id"] == QID:
            return case
    raise AssertionError(f"{QID} missing from pack")


def _answer_env(rows: list[dict[str, Any]], clock: dict[str, Any] | None) -> dict[str, Any]:
    env: dict[str, Any] = {
        "badge": "L0_CERTIFIED",
        "abstained": False,
        "rows": rows,
        "values": [],
    }
    if clock is not None:
        env["engine_clock"] = clock
    return env


def _clock(before: str, after: str | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "date_before": before,
        "date_after": after or before,
        "timezone": "Asia/Kuala_Lumpur",
        "source": "answer_submit",
    }


def test_audit_overdue_oracle_rows_on_two_dates_match_seed(tmp_path: Path) -> None:
    db = _seed(tmp_path)
    sql = _oracle_sql()
    got: dict[str, set[str]] = {}
    for as_of in ("2026-09-26", "2026-11-15"):
        rows, err = run_oracle_select(db, bind_oracle_as_of(sql, as_of))
        assert err is None, err
        got[as_of] = {str(r["supplier_id"]) for r in rows or []}
        assert got[as_of] == _expected(db, as_of), as_of
        assert got[as_of], f"zero rows at {as_of}"
    # Date-sensitive: SUP-02 (audited 2026-08-01) is overdue only at the later date.
    assert got["2026-09-26"] != got["2026-11-15"]


def test_judge_uses_engine_date_not_harness_clock(tmp_path: Path) -> None:
    db = _seed(tmp_path)
    oracles = load_oracles()
    case = _case()
    early, late = "2026-09-26", "2026-11-15"
    rows_early = [{"supplier_id": s} for s in sorted(_expected(db, early))]
    rows_late = [{"supplier_id": s} for s in sorted(_expected(db, late))]
    assert rows_early != rows_late

    ok_early = judge_envelope_detailed(
        case, _answer_env(rows_early, _clock(early)), oracle_db=db, oracles=oracles
    )
    ok_late = judge_envelope_detailed(
        case, _answer_env(rows_late, _clock(late)), oracle_db=db, oracles=oracles
    )
    assert (ok_early.verdict, ok_late.verdict) == ("OK", "OK")

    # The same rows judged at the other engine date are a different answer.
    crossed = judge_envelope_detailed(
        case, _answer_env(rows_early, _clock(late)), oracle_db=db, oracles=oracles
    )
    assert crossed.verdict == "WRONG"


def test_round_crossing_engine_midnight_is_invalid_never_wrong(tmp_path: Path) -> None:
    db = _seed(tmp_path)
    oracles = load_oracles()
    case = _case()
    # Rows deliberately wrong for both dates: still INVALID, not WRONG.
    env = _answer_env([{"supplier_id": "SUP-04"}], _clock("2026-09-26", "2026-09-27"))
    res = judge_envelope_detailed(case, env, oracle_db=db, oracles=oracles)
    assert res.verdict == "INVALID"
    assert res.reason == REASON_ENGINE_DATE_CROSSED
    cats = pack_category_report({"INVALID": 1, "OK": 51}, figure_label="fixture")
    assert cats["invalid_of"] == "1/52"
    assert cats["excluded_pending_scan"] == 0


def test_missing_engine_date_is_oracle_error_not_harness_clock(tmp_path: Path) -> None:
    db = _seed(tmp_path)
    today_rows = [
        {"supplier_id": s} for s in sorted(_expected(db, date.today().isoformat()))
    ]
    res = judge_envelope_detailed(
        _case(), _answer_env(today_rows, None), oracle_db=db, oracles=load_oracles()
    )
    assert res.verdict == "ORACLE_ERROR"
    assert res.reason == REASON_ENGINE_DATE_MISSING


def test_engine_clock_probes_only_clock_sql() -> None:
    seen: list[str] = []

    def submit(sql: str) -> QueryResult:
        seen.append(sql)
        if sql == PROBE_SQL:
            return QueryResult(
                ok=True,
                status="ok",
                output={"rows": [{"engine_date": "2026-09-26", "engine_timezone": "UTC"}]},
            )
        return QueryResult(ok=True, status="ok", output={"rows": [{"n": 1}]})

    plain = EngineClock(submit)
    plain.submit("SELECT COUNT(*) AS n FROM suppliers")
    assert seen == ["SELECT COUNT(*) AS n FROM suppliers"]
    assert plain.stamp is None

    seen.clear()
    clocked = EngineClock(submit)
    sql = _oracle_sql()
    assert sql_reads_clock(sql)
    clocked.submit(sql)
    assert seen == [PROBE_SQL, sql, PROBE_SQL]
    assert clocked.stamp == {
        "status": "ok",
        "date_before": "2026-09-26",
        "date_after": "2026-09-26",
        "timezone": "UTC",
        "source": "answer_submit",
    }
    abstain = {"badge": "ABSTAIN", "abstained": True, "rows": []}
    assert "engine_clock" not in (clocked.apply(abstain) or {})


# ---------------------------------------------------------------------------
# Customer envelope (rule 10a): POST /v1/chat/ask carries the engine date, and
# the row judge bound to that date agrees with the returned rows.
# ---------------------------------------------------------------------------


@dataclass
class _DuckEngine:
    """Cortex stand-in whose submit runs SQL on the seeded lake (the engine)."""

    db: Path
    timezone: str = "Asia/Kuala_Lumpur"
    sql_runs: list[str] = field(default_factory=list)
    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_bind_308")
        sql = str((getattr(req, "body", None) or {}).get("sql") or "")
        self.sql_runs.append(sql)
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            con.execute(f"SET TimeZone = '{self.timezone}'")
            cur = con.execute(sql)
            cols = [str(c[0]) for c in cur.description or []]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        for row in rows:
            for k, v in list(row.items()):
                if isinstance(v, date):
                    row[k] = v.isoformat()
        return QueryResult(ok=True, status="ok", run_id="run_sql_308", output={"rows": rows})

    def ledger_append(self, req: Any) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_308", hash="hash_308_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(answer="n/a", badge="abstain", abstained=True, audit_id="aud_308")


@pytest.fixture()
def minter() -> ManifestMinter:
    m = ManifestMinter()

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

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m


def test_chat_ask_envelope_carries_engine_date_and_judges_ok(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_api import settings as settings_mod

    db = _seed(tmp_path)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    pack = load_pack(ROOT / "tests/fixtures/curated_ceo/questions.yaml")
    space = resolve_space(_case(), pack["spaces"])
    sql = _oracle_sql()
    register_verified_query(space_id=space, question=QUESTION, sql=sql, path=db)

    engine = _DuckEngine(db)
    app = create_app()
    app.state.ask_service = Executor(
        cortex=engine,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=db,
    )
    app.state.cortex = engine
    r = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": QUESTION, "space_id": space, "session_id": "ses_308"},
    )
    settings_mod.get_settings.cache_clear()
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"].startswith("L0")

    clock = env["engine_clock"]
    assert clock["status"] == "ok"
    assert clock["date_before"] == clock["date_after"]
    assert clock["timezone"] == "Asia/Kuala_Lumpur"
    assert engine.sql_runs == [PROBE_SQL, sql, PROBE_SQL]

    as_of = clock["date_before"]
    want = _expected(db, as_of)
    assert {str(x["supplier_id"]) for x in env["rows"]} == want
    for sid in want:
        assert sid in env["text"]

    verdict = judge_envelope_detailed(
        _case(), env, oracle_db=db, oracles=load_oracles()
    )
    assert verdict.verdict == "OK", verdict
