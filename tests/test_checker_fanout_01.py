"""CHECKER-FANOUT-01: fan-out and ungrounded constant selects never take L2.

Extra columns stay served. The three list cases keep the badge on the demo
seed, where the join does not duplicate the subject key. Case ids live here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import DEMO_TABLES, connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import demo_ontology

_PACK = Path(__file__).resolve().parent / "fixtures" / "curated_ceo" / "questions.yaml"
_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "ask_guide" / "flag_off_52_f9ffc3e1.json"
_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_KEEP = ("cq_chemicals_list", "ops_chemicals_list", "cq_audit_overdue")
_FANOUT_SQL = (
    "SELECT f.sku AS sku, COUNT(*) AS n "
    "FROM inventory f JOIN locations l ON TRUE GROUP BY f.sku"
)
# Cross join duplicates the subject key in the served rows. No GROUP BY.
_DUP_ROWS_SQL = (
    "SELECT i.sku AS sku FROM inventory i JOIN locations l ON TRUE"
)
# Finance does not grant this table. The reply must not repeat its name.
_ALERTS_SQL = "SELECT alert_id, severity FROM alerts WHERE resolved = FALSE"


def _questions() -> dict[str, str]:
    doc = yaml.safe_load(_PACK.read_text(encoding="utf-8"))
    return {str(row["id"]): str(row["question"]) for row in doc["questions"]}


def _submit(db: Path):
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(d[0]) for d in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run-1", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="ent-fanout", hash="hash-fanout")


def _ask(
    db: Path,
    question: str,
    sql: str,
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    onto = load_verified_ontology(db)
    assert onto is not None

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        return {"query_sql": sql}

    env = maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(DEMO_TABLES),
        compute=compute,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )
    assert env is not None
    return env


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    minted = ManifestMinter()

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
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minted, "mint_manifest", _mint)
    monkeypatch.setattr(minted, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minted, "close", lambda: None)
    monkeypatch.setattr(minted, "invalidate", lambda *_a, **_k: None)
    key = MagicMock()
    key.kid = "test-kid"
    return minted


def test_extra_columns_keep_the_badge(tmp_path: Path) -> None:
    """List cases on this seed are an extra measure cell, not a fan-out."""
    from dms_executor.served_gate import served_result_reason

    db = tmp_path / "demo.duckdb"
    ensure_demo_warehouse(db)
    onto = demo_ontology(db)
    con = connect_file(db)
    try:
        assert onto.verify(con) == []
    finally:
        con.close()
    plans = {
        "cq_chemicals_list": onto.compile(
            "stock_value_myr",
            group_by=[("product", "sku")],
            filters=[("lot", "category", "=", "CHEMICALS")],
        ),
        "ops_chemicals_list": onto.compile(
            "stock_value_myr",
            group_by=[("product", "sku")],
            filters=[("lot", "category", "=", "CHEMICALS")],
        ),
        "cq_audit_overdue": onto.compile(
            "audit_overdue",
            group_by=[("supplier", "supplier_id")],
            filters=[("supplier", "last_audit_date", "<", "2026-07-11")],
        ),
    }
    questions = _questions()
    for case_id in _KEEP:
        compiled = plans[case_id]
        assert not isinstance(compiled, Exception)
        sql = compiled.sql
        assert served_result_reason(
            sql,
            warehouse=db,
            as_of="2026-10-09T00:00:00Z",
            ontology=onto,
        ) is None
        env = _ask(db, questions[case_id], sql, [])
        assert env["badge"] == "L2_VALIDATED", case_id
        assert env["abstained"] is False
        assert env["rows"]
        assert any(len(row) > 1 for row in env["rows"])
        assert_envelope_valid(env)


def test_real_fanout_refuses_the_badge(tmp_path: Path) -> None:
    db = tmp_path / "fan.duckdb"
    ensure_demo_warehouse(db)
    calls: list[dict[str, Any]] = []
    # "stock by location" abstains on main for a qualifier gap, before a badge.
    env = _ask(db, "list product skus", _FANOUT_SQL, calls)
    assert env["badge"] != "L2_VALIDATED"
    assert env["abstained"] is True
    blob = " ".join(str(item) for item in (env.get("assumptions") or []))
    blob = f"{blob} {env.get('text') or ''}"
    assert "fanout_subject_keys" in blob
    steps = [
        str((ctx.get("sql_loop_feedback") or {}).get("step") or "")
        for ctx in calls
        if isinstance(ctx.get("sql_loop_feedback"), dict)
    ]
    assert steps == ["self_correct", "richer_context", "stronger_tier"]
    richer = next(
        ctx for ctx in calls
        if (ctx.get("sql_loop_feedback") or {}).get("step") == "richer_context"
    )
    prompt = str(richer.get("schema_context") or "")
    assert prompt and prompt != "richer_context"
    assert "SCHEMA" in prompt
    assert_envelope_valid(env)


def test_constant_select_touching_no_granted_table_is_refused(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    """Direct grant refusal. Red on main: that tree still serves the select."""
    question = _questions()["trap_alerts_ungranted"]

    class _Cortex:
        def submit(self, req: Any) -> QueryResult:
            _ = req
            return QueryResult(ok=True, status="ok", run_id="run-bind")

        def ask(self, req: Any) -> AskResponse:
            _ = req
            return AskResponse(
                answer="1",
                badge="certified",
                sql_used="SELECT 1 AS n",
                rows=[{"n": 1}],
                assumptions="recorded",
                route="sql",
                audit_id="aud-constant",
            )

        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            _ = (question, kwargs)
            return {"ontology": {"metrics": []}}

        def ledger_append(self, req: Any) -> Any:
            _ = req
            return SimpleNamespace(entry_id="ent-constant", hash="hash-constant")

    db = tmp_path / "const.duckdb"
    ensure_demo_warehouse(db)
    exe = Executor(cortex=_Cortex(), minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(question, space_id=_FINANCE, session_id="ses_alerts")
    assert env["badge"] != "L2_VALIDATED"
    assert env["badge"] != "L0_CERTIFIED"
    assert env["abstained"] is True
    assert env.get("answer_id") == "ans_gen01_abstain"
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    text = str(env.get("text") or "")
    assert "ungranted" in notes
    assert "reconfirm" not in notes
    assert "reconfirm" not in text
    # The served statement cites no relation, so the reply must not name one.
    folded = f"{text} {notes}".lower()
    assert "alerts" not in folded
    assert_envelope_valid(env)


def test_duplicated_subject_rows_never_take_l2(tmp_path: Path) -> None:
    """Cartesian join. Main badges L2_VALIDATED. Head refuses fanout_subject_keys.

    The question is one main serves. A qualifier-gap question abstains on main
    for a different reason and is not this must-fail.
    """
    db = tmp_path / "dup.duckdb"
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        rows = con.execute(_DUP_ROWS_SQL).fetchall()
    finally:
        con.close()
    skus = [row[0] for row in rows]
    # 7 inventory keys crossed with every location: 35 served rows.
    assert len(skus) == 35
    assert len(set(skus)) == 7
    env = _ask(db, "list product skus", _DUP_ROWS_SQL, [])
    # Red on main: this fails because the main-side badge is L2_VALIDATED.
    assert env["badge"] != "L2_VALIDATED", "main-side badge is L2_VALIDATED"
    assert env["abstained"] is True
    blob = f"{env.get('text') or ''} {' '.join(str(a) for a in (env.get('assumptions') or []))}"
    assert "fanout_subject_keys" in blob
    assert_envelope_valid(env)


def test_route_alone_does_not_grant_l2() -> None:
    refused = build_answer_envelope(
        answer_id="ans_route",
        text="Revenue was 100.",
        badge="generated",
        sql_used="SELECT revenue_myr FROM transactions",
        rows=[{"revenue_myr": 100.0}],
        assumptions=["route only"],
        audit_id="aud-route",
    )
    assert refused["badge"] != "L2_VALIDATED"
    assert refused["abstained"] is True
    passed = build_answer_envelope(
        answer_id="ans_route_ok",
        text="Revenue was 100.",
        badge="generated",
        gate_passed=True,
        sql_used="SELECT revenue_myr FROM transactions",
        rows=[{"revenue_myr": 100.0}],
        assumptions=["gate passed"],
        audit_id="aud-route-ok",
    )
    assert passed["badge"] == "L2_VALIDATED"
    assert passed["abstained"] is False


def test_currently_correct_generated_sql_keeps_the_gate(tmp_path: Path) -> None:
    db = tmp_path / "keep.duckdb"
    ensure_demo_warehouse(db)
    onto = demo_ontology(db)
    con = connect_file(db)
    try:
        onto.verify(con)
    finally:
        con.close()
    from dms_executor.served_gate import served_result_reason

    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    checked = 0
    for row in golden:
        env = row["env"]
        if env.get("badge") != "L2_VALIDATED":
            continue
        sql = str(env.get("sql_used") or "")
        why = served_result_reason(
            sql,
            warehouse=db,
            as_of="2026-10-09T00:00:00Z",
            ontology=onto,
        )
        assert why is None, row["id"]
        checked += 1
    assert checked >= 17


_SKU_PER_LOCATION = (
    "SELECT l.location_id AS location_id, COUNT(i.sku) AS sku_count "
    "FROM locations l JOIN inventory i ON i.location_id = l.location_id "
    "GROUP BY l.location_id"
)
_LINES_PER_SUPPLIER = (
    "SELECT s.supplier_id AS supplier_id, COUNT(*) AS line_count "
    "FROM suppliers s JOIN inventory i ON i.supplier_id = s.supplier_id "
    "GROUP BY s.supplier_id"
)
_SUM_CAPACITY = (
    "SELECT l.location_id AS location_id, SUM(l.capacity_kg) AS capacity "
    "FROM locations l JOIN inventory i ON i.location_id = l.location_id "
    "GROUP BY l.location_id"
)
_AUDIT_CLOCK = (
    "SELECT supplier_id, COUNT(*) FILTER (WHERE CAST(last_audit_date AS DATE) "
    "< CURRENT_DATE - INTERVAL 90 DAY) AS n FROM suppliers GROUP BY supplier_id"
)


def _executed(db: Path, sql: str) -> list[dict[str, Any]]:
    con = connect_file(db)
    try:
        cur = con.execute(sql)
        cols = [str(d[0]) for d in (cur.description or [])]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _cells(rows: list[dict[str, Any]]) -> list[tuple[tuple[str, Any], ...]]:
    def cell(value: Any) -> Any:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    return sorted(
        tuple(sorted((str(key), cell(val)) for key, val in row.items())) for row in rows
    )


def test_one_to_many_counts_keep_l2_and_wrong_grain_sum_does_not(tmp_path: Path) -> None:
    """GROUP BY grain, not the FROM-table key. Counts stay. A fanned SUM does not."""
    from dms_executor.served_gate import served_result_reason

    db = tmp_path / "grain.duckdb"
    ensure_demo_warehouse(db)
    onto = demo_ontology(db)
    con = connect_file(db)
    try:
        assert onto.verify(con) == []
    finally:
        con.close()
    bound = "2026-10-09T00:00:00Z"
    for sql in (_SKU_PER_LOCATION, _LINES_PER_SUPPLIER):
        assert served_result_reason(sql, warehouse=db, as_of=bound, ontology=onto) is None
        env = _ask(db, "list product skus", sql, [])
        assert env["badge"] == "L2_VALIDATED"
        assert _cells(env["rows"]) == _cells(_executed(db, sql))
    refused = _ask(db, "list product skus", _SUM_CAPACITY, [])
    assert refused["badge"] != "L2_VALIDATED"
    assert refused["abstained"] is True
    notes = " ".join(str(a) for a in (refused.get("assumptions") or []))
    blob = f"{refused.get('text') or ''} {notes}"
    assert "fanout_subject_keys" in blob
    lot = onto.compile("stock_value_myr", group_by=[("product", "sku")])
    assert served_result_reason(lot.sql, warehouse=db, as_of=bound, ontology=onto) is None
    served = _ask(db, "list product skus", lot.sql, [])
    assert served["badge"] == "L2_VALIDATED"
    assert _cells(served["rows"]) == _cells(_executed(db, lot.sql))


def test_ladder_rung_stamps_the_step_that_served(tmp_path: Path) -> None:
    db = tmp_path / "rung.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db)
    assert onto is not None
    calls: list[dict[str, Any]] = []
    good = "SELECT sku FROM inventory"

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(ctx))
        step = str((ctx.get("sql_loop_feedback") or {}).get("step") or "")
        if step == "richer_context":
            return {"query_sql": good}
        return {"query_sql": _DUP_ROWS_SQL}

    env = maybe_generative_ask(
        "list product skus",
        warehouse=db,
        grantable=set(DEMO_TABLES),
        compute=compute,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("ladder_rung") == "richer_context"
    richer = next(
        ctx for ctx in calls
        if (ctx.get("sql_loop_feedback") or {}).get("step") == "richer_context"
    )
    prompt = str(richer.get("schema_context") or "")
    assert prompt and prompt != "richer_context"
    assert "SCHEMA" in prompt
    assert_envelope_valid(env)


def test_audit_clock_uses_bound_as_of_at_0300_myt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """03:00 Asia/Kuala_Lumpur is still the previous UTC day. The bound as_of wins."""
    import os
    import time
    from datetime import UTC, datetime

    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kuala_Lumpur"
    time.tzset()
    db = tmp_path / "audit.duckdb"
    ensure_demo_warehouse(db)
    onto = demo_ontology(db)
    con = connect_file(db)
    try:
        host = str(con.execute("SELECT CAST(CURRENT_DATE AS VARCHAR)").fetchone()[0])[:10]
    finally:
        con.close()
    # 03:00 MYT is 19:00 the previous UTC day. Force that day to differ from the host date.
    as_of_day = "2026-10-08" if host != "2026-10-08" else "2026-10-07"
    as_of = f"{as_of_day}T19:00:00Z"
    assert host != as_of_day
    from dms_executor.served_gate import served_result_reason

    assert served_result_reason(
        _AUDIT_CLOCK, warehouse=db, as_of=as_of, ontology=onto
    ) is None
    assert served_result_reason(
        "SELECT COUNT(*) AS n FROM suppliers WHERE last_audit_date "
        "< DATE '2020-01-01' - INTERVAL 90 DAY",
        warehouse=db,
        as_of=as_of,
        ontology=onto,
    ) == "as_of_window"

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            _ = tz
            year = int(as_of_day[0:4])
            month = int(as_of_day[5:7])
            day = int(as_of_day[8:10])
            return datetime(year, month, day, 19, 0, tzinfo=UTC)

    monkeypatch.setattr("dms_executor.generative_ask.datetime", _Clock)
    try:
        env = _ask(db, "list product skus", _AUDIT_CLOCK, [])
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert "as_of_window" not in " ".join(str(a) for a in (env.get("assumptions") or []))


def test_http_ask_three_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cartesian and wrong-grain SUM refuse on every route. Counts and lot serve."""
    import os
    import time
    from datetime import UTC, datetime

    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from cortex_contract.execution import QueryResult
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kuala_Lumpur"
    time.tzset()
    db = tmp_path / "http.duckdb"
    ensure_demo_warehouse(db)
    onto = demo_ontology(db)
    con = connect_file(db)
    try:
        assert onto.verify(con) == []
        host = str(con.execute("SELECT CAST(CURRENT_DATE AS VARCHAR)").fetchone()[0])[:10]
    finally:
        con.close()
    lot = onto.compile("stock_value_myr", group_by=[("product", "sku")])
    assert getattr(lot, "sql", None), lot
    as_of_day = "2026-10-08" if host != "2026-10-08" else "2026-10-07"
    as_of = f"{as_of_day}T19:00:00Z"

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            _ = tz
            year = int(as_of_day[0:4])
            month = int(as_of_day[5:7])
            day = int(as_of_day[8:10])
            return datetime(year, month, day, 19, 0, tzinfo=UTC)

    monkeypatch.setattr("dms_executor.generative_ask.datetime", _Clock)
    monkeypatch.setattr("dms_executor.datetime_now", lambda: as_of)

    cases = (
        ("cartesian", _DUP_ROWS_SQL, False),
        ("sum_capacity", _SUM_CAPACITY, False),
        ("skus_per_location", _SKU_PER_LOCATION, True),
        ("lines_per_supplier", _LINES_PER_SUPPLIER, True),
        ("lot_of_product", lot.sql, True),
        ("audit_0300_myt", _AUDIT_CLOCK, True),
        # Cortex ask SQL, not insights query_sql. hold_ungrounded_sql sees it.
        ("alerts_finance", _ALERTS_SQL, False),
    )

    class _Cortex:
        def __init__(self, sql: str, *, generated: bool, via_ask: bool) -> None:
            self.sql = sql
            self.generated = generated
            self.via_ask = via_ask
            self.asks: list[Any] = []

        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
            _ = (question, kwargs)
            if self.generated or self.via_ask:
                return None
            return {"query_sql": self.sql}

        def submit(self, req: Any) -> QueryResult:
            body = getattr(req, "body", None) or {}
            sql = str(body.get("sql") or self.sql)
            rows = _executed(db, sql) if sql.strip() else []
            return QueryResult(ok=True, status="ok", run_id="run-http", output={"rows": rows})

        def ask(self, req: AskRequest) -> AskResponse:
            self.asks.append(req)
            rows = _executed(db, self.sql)
            return AskResponse(
                answer="served",
                badge="generated",
                sql_used=self.sql,
                rows=rows,
                assumptions="recorded",
                route="generated",
                audit_id="aud-http",
            )

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            return LedgerAppendResponse(entry_id="ent-http", hash="hash-http")

    def post(route: str, sql: str, *, via_ask: bool = False) -> dict[str, Any]:
        monkeypatch.setenv("DMS_ASK_MODE", "live")
        monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
        monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
        if route == "cloop_b":
            monkeypatch.setenv("DMS_CLOOP_B", "1")
        else:
            monkeypatch.delenv("DMS_CLOOP_B", raising=False)
        monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
        settings_mod.get_settings.cache_clear()
        minted = ManifestMinter()

        def _mint(acl: SessionAcl) -> Manifest:
            return Manifest(
                session_id=acl.session_id,
                org_id=acl.org_id,
                space_id=acl.space_id,
                pool_id=acl.pool_id,
                issuer_key_id="test-kid",
                allowed_paths=list(acl.allowed_paths),
                row_predicates=dict(acl.row_predicates),
                issued_at="2026-10-09T00:00:00+00:00",
                expires_at="2026-10-09T01:00:00+00:00",
                signature="dGVzdHNpZw",
            )

        minted.mint_manifest = _mint  # type: ignore[method-assign]
        minted.fetch_intermediate = lambda: None  # type: ignore[method-assign]
        minted.close = lambda: None  # type: ignore[method-assign]
        minted.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
        cortex = _Cortex(sql, generated=(route == "generated"), via_ask=via_ask)
        app = create_app()
        app.state.ask_service = Executor(
            cortex=cortex, minter=minted, warehouse_path=db  # type: ignore[arg-type]
        )
        app.state.cortex = cortex
        res = TestClient(app).post(
            "/v1/chat/ask",
            json={
                "question": "list product skus",
                "space_id": _FINANCE,
                "session_id": "ses_fan_http",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        if route == "generated" or via_ask:
            assert cortex.asks, route
        return body

    try:
        for name, sql, serve in cases:
            via_ask = name == "alerts_finance"
            for route in ("flag_off", "cloop_b", "generated"):
                env = post(route, sql, via_ask=via_ask)
                label = f"{route} {name}"
                if serve:
                    assert env["badge"] == "L2_VALIDATED", label
                    assert env["abstained"] is False, label
                    assert _cells(env["rows"]) == _cells(_executed(db, sql)), label
                else:
                    assert env["badge"] != "L2_VALIDATED", label
                    assert env["abstained"] is True, label
                    if via_ask:
                        notes = " ".join(str(a) for a in (env.get("assumptions") or []))
                        folded = " ".join(
                            (
                                str(env.get("text") or ""),
                                notes,
                                str(env.get("sql_used") or ""),
                                str(env.get("rows") or ""),
                            )
                        ).lower()
                        assert env["badge"] == "ABSTAIN", label
                        assert env["badge"] != "L0_CERTIFIED", label
                        assert env.get("answer_id") == "ans_gen01_abstain", label
                        assert "ungranted" in notes, label
                        assert "reconfirm" not in folded, label
                        assert "alerts" not in folded, label
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()
