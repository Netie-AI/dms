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
        # The wiring.py shape: no warehouse_path. DMS_WAREHOUSE_DB is the lake.
        app.state.ask_service = Executor(cortex=cortex, minter=minted)  # type: ignore[arg-type]
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
                assert_envelope_valid(env)
                if serve:
                    assert env["badge"] == "L2_VALIDATED", label
                    assert env["abstained"] is False, label
                    assert "cannot certify" not in str(env.get("text") or ""), label
                    assert str(env.get("text") or "").strip(), label
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


# --- Takeover findings: production Executor construction, file readers ---


def _prod_post(
    db: Path,
    sql: str,
    monkeypatch: pytest.MonkeyPatch,
    minted: ManifestMinter,
    *,
    route: str = "generated",
    pass_warehouse: bool = False,
    rows_sql: str | None = None,
    badge: str = "generated",
) -> tuple[dict[str, Any], Any]:
    """POST /v1/chat/ask with the wiring.py shape: no warehouse_path argument.

    The lake is reached the way production reaches it, through
    DMS_WAREHOUSE_DB and the default warehouse_path(). ``pass_warehouse``
    hands the file to the Executor so a test can isolate one concern.
    ``rows_sql`` is what the fake Cortex runs when its cited SQL uses an alias
    the lake does not hold.
    """
    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    class _Cortex:
        def __init__(self) -> None:
            self.asks: list[Any] = []

        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
            _ = (question, kwargs)
            return None if route == "generated" else {"query_sql": sql}

        def submit(self, req: Any) -> QueryResult:
            body = getattr(req, "body", None) or {}
            run_sql = str(rows_sql or body.get("sql") or sql)
            rows = _executed(db, run_sql) if run_sql.strip() else []
            return QueryResult(ok=True, status="ok", run_id="run-prod", output={"rows": rows})

        def ask(self, req: AskRequest) -> AskResponse:
            self.asks.append(req)
            return AskResponse(
                answer="served",
                badge=badge,
                sql_used=sql,
                rows=_executed(db, rows_sql or sql),
                assumptions="recorded",
                route="generated",
                audit_id="aud-prod",
            )

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            return LedgerAppendResponse(entry_id="ent-prod", hash="hash-prod")

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    if route == "cloop_b":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
    else:
        monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    settings_mod.get_settings.cache_clear()
    cortex = _Cortex()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minted,
        warehouse_path=db if pass_warehouse else None,
    )
    app.state.cortex = cortex
    res = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": "list product skus", "space_id": _FINANCE, "session_id": "ses_prod"},
    )
    assert res.status_code == 200, res.text
    return res.json(), cortex


def test_generated_route_serves_with_the_production_executor_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """wiring.py builds Executor without warehouse_path. The gate must still run.

    Before the fix the gate had no warehouse file, so every correct Cortex
    generated answer abstained with reconfirm:inputs_missing.
    """
    db = tmp_path / "prod.duckdb"
    ensure_demo_warehouse(db)
    env, cortex = _prod_post(db, _SKU_PER_LOCATION, monkeypatch, minter)
    assert cortex.asks
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert "inputs_missing" not in " ".join(str(a) for a in (env.get("assumptions") or []))
    assert _cells(env["rows"]) == _cells(_executed(db, _SKU_PER_LOCATION))
    assert "reconfirm" not in str(env.get("text") or "")


def test_generated_route_still_refuses_fanout_with_the_production_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The default warehouse path must feed the grain probe, not only the happy path."""
    db = tmp_path / "prod_fan.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, _DUP_ROWS_SQL, monkeypatch, minter)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "fanout_subject_keys" in str(env.get("text") or "")


@pytest.mark.parametrize("route", ["flag_off", "cloop_b", "generated"])
def test_file_reader_sql_is_refused_before_any_local_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
) -> None:
    """A file reader is always refused and the DMS process never runs it.

    The served-result probe runs SQL locally. It must not see SQL the one
    grant function rejects as hostile, on any route.
    """
    import dms_executor.served_gate as gate

    csv = tmp_path / "three.csv"
    csv.write_text("k\n1\n2\n3\n", encoding="utf-8")
    sql = (
        "SELECT i.sku AS sku, COUNT(*) AS n FROM inventory i "
        f"JOIN read_csv('{csv.as_posix()}') c ON TRUE GROUP BY i.sku"
    )
    db = tmp_path / "hostile.duckdb"
    ensure_demo_warehouse(db)
    probed: list[str] = []
    real = gate._row_counts

    def spy(path: Path, probe: str) -> bool:
        probed.append(probe)
        return real(path, probe)

    monkeypatch.setattr(gate, "_row_counts", spy)
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["abstained"] is True, route
    assert env["badge"] == "ABSTAIN", route
    assert env.get("rows") in (None, []), route
    text = str(env.get("text") or "")
    assert "fanout_subject_keys" not in text, route
    assert not probed, f"{route} ran the served-result probe on refused SQL: {probed}"


def test_qualified_upload_grant_does_not_grant_the_demo_lake_table(tmp_path: Path) -> None:
    """A grant on bronze.alerts is an upload. It does not open the lake table alerts."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "grant.duckdb"
    ensure_demo_warehouse(db)
    lake = "SELECT alert_id FROM alerts"
    assert (
        validate_compiled_sql(lake, grantable={"bronze.alerts"}, warehouse=db)
        == "ungranted:alerts"
    )
    # The upload itself, cited by its bare label, stays granted by its qualified grant.
    sheet = "SELECT * FROM q3_sales_sheet"
    assert validate_compiled_sql(
        sheet, grantable={"bronze.q3_sales_sheet"}, warehouse=db
    ) != "ungranted:q3_sales_sheet"
    # The lake table granted by its own name still passes the grant step.
    assert (
        validate_compiled_sql(lake, grantable={"alerts"}, warehouse=db) != "ungranted:alerts"
    )


_READERS = (
    "read_text('{p}')",
    "read_blob('{p}')",
    "sniff_csv('{p}')",
    "read_ndjson('{p}')",
    "glob('{d}/*')",
    "'{p}'",
    '"{d}/x.csv"',
)


@pytest.mark.parametrize("reader", _READERS)
def test_every_grant_rejection_is_refused_before_any_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    reader: str,
) -> None:
    """validate_compiled_sql rejects these as ungranted or EXPLAIN-denied.

    Only read_csv was refused before the probe; the rest were executed by the
    served-result probe through connect_file on the way to a verdict.
    """
    import dms_executor.served_gate as gate

    note = tmp_path / "note.txt"
    note.write_text("k\n1\n2\n3\n", encoding="utf-8")
    (tmp_path / "x.csv").write_text("k\n1\n", encoding="utf-8")
    src = reader.format(p=note.as_posix(), d=tmp_path.as_posix())
    sql = (
        "SELECT i.sku AS sku, COUNT(*) AS n FROM inventory i "
        f"JOIN {src} c ON TRUE GROUP BY i.sku"
    )
    db = tmp_path / "rej.duckdb"
    ensure_demo_warehouse(db)
    probed: list[str] = []
    real = gate._row_counts

    def spy(path: Path, probe: str) -> bool:
        probed.append(probe)
        return real(path, probe)

    monkeypatch.setattr(gate, "_row_counts", spy)
    env, _ = _prod_post(
        db,
        sql,
        monkeypatch,
        minter,
        pass_warehouse=True,
        # The fake Cortex must not run the reader itself; DMS is what is tested.
        rows_sql="SELECT i.sku AS sku, 3 AS n FROM inventory i",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True, reader
    assert env["badge"] == "ABSTAIN", reader
    assert env.get("rows") in (None, []), reader
    assert "fanout_subject_keys" not in str(env.get("text") or ""), reader
    assert not probed, f"probe ran on SQL the grant function rejects: {probed}"


def test_warehouse_alias_of_a_granted_table_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """R-0005: a Cortex answer citing warehouse_<granted table> is served."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "alias.duckdb"
    ensure_demo_warehouse(db)
    aliased = (
        "SELECT l.location_id AS location_id, COUNT(i.sku) AS sku_count "
        "FROM warehouse_locations l JOIN warehouse_inventory i "
        "ON i.location_id = l.location_id GROUP BY l.location_id"
    )
    grant = {"inventory", "locations"}
    assert validate_compiled_sql(aliased, grantable=grant, warehouse=db) is None
    assert validate_compiled_sql(aliased, grantable={"inventory"}, warehouse=db) == (
        "ungranted:warehouse_locations"
    )
    env, _ = _prod_post(
        db, aliased, monkeypatch, minter, pass_warehouse=True, rows_sql=_SKU_PER_LOCATION
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert _cells(env["rows"]) == _cells(_executed(db, _SKU_PER_LOCATION))


def test_warehouse_alias_does_not_hide_a_fanout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The aliased SQL is probed on the lake names, so a fan-out still refuses."""
    db = tmp_path / "alias_fan.duckdb"
    ensure_demo_warehouse(db)
    aliased = "SELECT i.sku AS sku FROM warehouse_inventory i JOIN warehouse_locations l ON TRUE"
    env, _ = _prod_post(
        db, aliased, monkeypatch, minter, pass_warehouse=True, rows_sql=_DUP_ROWS_SQL
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "fanout_subject_keys" in str(env.get("text") or "")


# --- Takeover round 3: CTE scope, generator tables, fail closed ---

_CTE_CARTESIAN = (
    "WITH l AS (SELECT location_id FROM locations) "
    "SELECT i.sku AS sku FROM inventory i JOIN l ON TRUE"
)
_CTE_TWO_CARTESIAN = (
    "WITH inv AS (SELECT * FROM inventory), loc AS (SELECT * FROM locations) "
    "SELECT inv.sku AS sku FROM inv JOIN loc ON TRUE"
)
_CTE_PER_SUPPLIER = (
    "WITH s AS (SELECT supplier_id, COUNT(*) AS n FROM inventory GROUP BY supplier_id) "
    "SELECT s.supplier_id AS supplier_id, s.n AS n FROM s"
)
_CTE_JOIN_SUPPLIER = (
    "WITH s AS (SELECT supplier_id, COUNT(*) AS n FROM inventory GROUP BY supplier_id) "
    "SELECT sp.supplier_name AS supplier_name, s.n AS n "
    "FROM s JOIN suppliers sp ON sp.supplier_id = s.supplier_id"
)
_DATE_SPINE = (
    "SELECT d.day AS day, COUNT(t.txn_id) AS n "
    "FROM generate_series(DATE '2026-01-01', DATE '2026-01-05', INTERVAL 1 DAY) AS d(day) "
    "LEFT JOIN transactions t ON CAST(t.ts AS DATE) = d.day "
    "GROUP BY d.day ORDER BY d.day"
)
_MONTH_SPINE = (
    "SELECT r.m AS month_no, COUNT(t.txn_id) AS n "
    "FROM range(1, 13) AS r(m) LEFT JOIN transactions t ON month(t.ts) = r.m "
    "GROUP BY r.m ORDER BY r.m"
)
# The recursive term reads the CTE; the first term does not.
_RECURSIVE_SPINE = (
    "WITH RECURSIVE d(day) AS (SELECT DATE '2026-01-01' "
    "UNION ALL SELECT day + INTERVAL 1 DAY FROM d WHERE day < DATE '2026-01-05') "
    "SELECT d.day AS day, COUNT(t.txn_id) AS n FROM d "
    "LEFT JOIN transactions t ON CAST(t.ts AS DATE) = d.day GROUP BY d.day ORDER BY d.day"
)
# DuckDB reads the base table `alerts` in the first term, whatever the CTE is called.
_RECURSIVE_SHADOW = (
    "WITH RECURSIVE alerts(a) AS (SELECT 1 FROM alerts "
    "UNION ALL SELECT a + 1 FROM alerts WHERE a < 3) SELECT a FROM alerts"
)
_SPINE_NO_COLUMN_LIST = (
    "SELECT COUNT(*) AS n FROM generate_series(1, 5) JOIN suppliers ON TRUE"
)
_ROUTES = ("generated", "flag_off", "cloop_b")


def _spy_probes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import dms_executor.served_gate as gate

    probed: list[str] = []
    real = gate._row_counts

    def spy(path: Path, probe: str) -> bool:
        probed.append(probe)
        return real(path, probe)

    monkeypatch.setattr(gate, "_row_counts", spy)
    return probed


def _visible(env: dict[str, Any]) -> str:
    return " ".join(
        (
            str(env.get("text") or ""),
            " ".join(str(a) for a in (env.get("assumptions") or [])),
            str(env.get("sql_used") or ""),
            str(env.get("rows") or ""),
        )
    ).lower()


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("sql", [_CTE_CARTESIAN, _CTE_TWO_CARTESIAN], ids=["one_cte", "two_ctes"])
def test_cte_fanout_never_takes_l2_on_any_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    sql: str,
) -> None:
    """A CTE alias is not a cited relation, so the grain probe still runs on it.

    The alias was read as an ungranted relation that the ask route did not
    refuse. It skipped the probe and shipped 35 rows for 7 distinct skus at L2.
    """
    db = tmp_path / "cte_fan.duckdb"
    ensure_demo_warehouse(db)
    probed = _spy_probes(monkeypatch)
    env, _ = _prod_post(
        db, sql, monkeypatch, minter, route=route, pass_warehouse=True, rows_sql=_DUP_ROWS_SQL
    )
    assert_envelope_valid(env)
    assert env["badge"] != "L2_VALIDATED", route
    assert env["abstained"] is True, route
    assert env["badge"] == "ABSTAIN", route
    assert env.get("rows") in (None, []), route
    assert probed, f"{route} never probed the CTE query's grain"
    if route == "generated":
        assert "fanout_subject_keys" in str(env.get("text") or "")


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("sql", [_CTE_PER_SUPPLIER, _CTE_JOIN_SUPPLIER], ids=["alone", "joined"])
def test_cte_answer_over_granted_tables_keeps_l2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    sql: str,
) -> None:
    """A legitimate CTE over granted tables is served with the rows it computed."""
    db = tmp_path / "cte_ok.duckdb"
    ensure_demo_warehouse(db)
    probed = _spy_probes(monkeypatch)
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["abstained"] is False, (route, env.get("text"))
    assert env["badge"] == "L2_VALIDATED", (route, env.get("assumptions"))
    expected = _executed(db, sql)
    assert expected
    assert _cells(env["rows"]) == _cells(expected), route
    assert str(env.get("text") or "").strip(), route
    assert "cannot certify" not in str(env.get("text") or ""), route
    if sql == _CTE_JOIN_SUPPLIER:
        assert probed, f"{route} served a joined CTE query without probing its grain"


def test_grant_step_reads_real_tables_by_scope(tmp_path: Path) -> None:
    """One grant function. A CTE alias is never a relation; a real table always is."""
    from dms_executor.generative_ask import cited_relations, validate_compiled_sql

    db = tmp_path / "scope.duckdb"
    ensure_demo_warehouse(db)
    grant = {"inventory", "locations", "suppliers", "transactions"}

    def check(sql: str, granted: set[str] | None = None) -> str | None:
        return validate_compiled_sql(sql, grantable=granted or grant, warehouse=db)

    assert check(_CTE_CARTESIAN) is None
    assert check(_CTE_TWO_CARTESIAN) is None
    assert check(_CTE_JOIN_SUPPLIER) is None
    assert cited_relations(_CTE_JOIN_SUPPLIER) == {"inventory", "suppliers"}
    # The CTE body names an ungranted table: that table is the rejection, not the alias.
    assert check("WITH a AS (SELECT * FROM alerts) SELECT * FROM a") == "ungranted:alerts"
    # An alias that shadows an ungranted lake name hides nothing it does not define.
    assert check("WITH alerts AS (SELECT sku FROM inventory) SELECT sku FROM alerts") is None
    # The body of a CTE named like a granted table reads the real table beneath it.
    assert (
        check("WITH inventory AS (SELECT * FROM alerts) SELECT * FROM inventory")
        == "ungranted:alerts"
    )
    # A CTE in one scope does not hide the real table used in another scope.
    assert (
        check(
            "SELECT x.a FROM (WITH alerts AS (SELECT 1 AS a) SELECT a FROM alerts) x "
            "JOIN alerts ON TRUE"
        )
        == "ungranted:alerts"
    )
    assert (
        check(
            "WITH alerts AS (SELECT 1 AS a) SELECT a FROM alerts "
            "UNION ALL SELECT alert_id FROM (SELECT alert_id FROM main.alerts) q"
        )
        == "ungranted:alerts"
    )
    # A recursive CTE's first term does not see the CTE: DuckDB reads the base table.
    assert check(_RECURSIVE_SHADOW) == "ungranted:alerts"
    assert check(_RECURSIVE_SPINE) is None
    assert cited_relations(_RECURSIVE_SPINE) == {"transactions"}
    # DuckDB names are case-insensitive.
    assert check("WITH L AS (SELECT location_id FROM locations) SELECT location_id FROM l") is None
    # Names that are not in the grant stay rejected, whatever scope they sit in.
    assert check("WITH a AS (SELECT 1 AS x) SELECT a.x FROM a JOIN shipments ON TRUE") == (
        "ungranted:shipments"
    )


def test_generator_tables_are_not_file_readers(tmp_path: Path) -> None:
    """range, generate_series and unnest are generators. Every other table function is not."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "gen.duckdb"
    ensure_demo_warehouse(db)
    grant = {"inventory", "suppliers", "transactions"}

    def check(sql: str) -> str | None:
        return validate_compiled_sql(sql, grantable=grant, warehouse=db)

    assert check(_DATE_SPINE) is None
    assert check(_MONTH_SPINE) is None
    assert check(_SPINE_NO_COLUMN_LIST) is None
    assert check(
        "SELECT g.d AS d FROM range(DATE '2026-01-01', DATE '2026-01-04', INTERVAL 1 DAY) "
        "AS g(d) CROSS JOIN suppliers"
    ) is None
    for sql in (
        "SELECT s.supplier_id AS k FROM suppliers s JOIN duckdb_settings() d ON TRUE",
        "SELECT s.supplier_id AS k FROM suppliers s JOIN pragma_table_info('inventory') p ON TRUE",
        "SELECT s.supplier_id AS k FROM suppliers s JOIN duckdb_tables() p ON TRUE",
        # A generator does not launder a reader that sits beside it.
        "SELECT g.i AS k FROM generate_series(1, 2) AS g(i) JOIN duckdb_settings() d ON TRUE",
        "SELECT g.i AS k FROM generate_series(1, (SELECT COUNT(*) FROM duckdb_tables())) g(i)",
        "SELECT s.supplier_id AS k FROM suppliers s JOIN read_text('/etc/hostname') p ON TRUE",
    ):
        assert check(sql) == "hostile_sql:file_reader", sql
    # read_csv is also caught by the pattern screen that runs first.
    assert str(check("SELECT * FROM read_csv('x.csv')")).startswith("hostile_sql:")
    # A generator over no table is a literal select, not an answer about the lake.
    assert check("SELECT d.day AS day FROM generate_series(1, 5) AS d(day)") == "ungranted"
    assert check("SELECT r.m AS m FROM range(1, 13) AS r(m)") == "ungranted"


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("sql,rows", [(_DATE_SPINE, 5), (_MONTH_SPINE, 12)], ids=["gs", "range"])
def test_generator_spine_over_granted_tables_is_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    sql: str,
    rows: int,
) -> None:
    """R-0005: main served a date or month spine at L2; it must not turn hostile."""
    db = tmp_path / "spine.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["abstained"] is False, (route, env.get("text"))
    assert env["badge"] == "L2_VALIDATED", (route, env.get("assumptions"))
    assert len(env["rows"]) == rows, route
    # The day column is serialised (and masked) by the envelope; the counts are not.
    expected = _executed(db, sql)
    assert [r["n"] for r in env["rows"]] == [r["n"] for r in expected], route
    assert "hostile" not in _visible(env), route
    assert "cannot certify" not in str(env.get("text") or ""), route


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT i.sku AS sku FROM inventory i JOIN duckdb_settings() d ON TRUE",
        "SELECT i.sku AS sku FROM inventory i JOIN pragma_table_info('inventory') p ON TRUE",
        "SELECT g.i AS k FROM generate_series(1, 2) AS g(i) JOIN duckdb_settings() d ON TRUE",
        "SELECT g.i AS k FROM generate_series(1, 5) AS g(i)",
    ],
    ids=["settings", "pragma", "generator_beside_reader", "literal_generator"],
)
@pytest.mark.parametrize("route", _ROUTES)
def test_metadata_functions_and_literal_generators_stay_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    sql: str,
) -> None:
    db = tmp_path / "meta.duckdb"
    ensure_demo_warehouse(db)
    probed = _spy_probes(monkeypatch)
    env, _ = _prod_post(
        db,
        sql,
        monkeypatch,
        minter,
        route=route,
        pass_warehouse=True,
        rows_sql="SELECT i.sku AS sku FROM inventory i",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True, route
    assert env["badge"] == "ABSTAIN", route
    assert env.get("rows") in (None, []), route
    assert not probed, f"{route} probed refused SQL: {probed}"


# The grant function rejects each of these and grant_hold does not refuse them
# (not a demo-lake name). They used to skip the probe and keep L2.
_REJECTED_NOT_HELD = (
    (
        "bronze_label",
        "SELECT i.sku AS sku FROM inventory i JOIN locations l ON TRUE "
        "WHERE i.sku NOT IN (SELECT sku FROM q3_sales_sheet)",
        "q3_sales_sheet",
    ),
    (
        "double_alias",
        "SELECT i.sku AS sku FROM warehouse_warehouse_inventory i JOIN locations l ON TRUE",
        "warehouse_warehouse_inventory",
    ),
    (
        "explain_miss",
        "SELECT i.sku AS sku, i.no_such_column AS c FROM inventory i JOIN locations l ON TRUE",
        "no_such_column",
    ),
)


@pytest.mark.parametrize("case", _REJECTED_NOT_HELD, ids=[c[0] for c in _REJECTED_NOT_HELD])
def test_rejected_unprobed_sql_never_takes_l2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    case: tuple[str, str, str],
) -> None:
    """Fail closed: a grant rejection that is not refused outright still loses the badge."""
    _, sql, name = case
    db = tmp_path / "rej_open.duckdb"
    ensure_demo_warehouse(db)
    probed = _spy_probes(monkeypatch)
    env, cortex = _prod_post(
        db, sql, monkeypatch, minter, pass_warehouse=True, rows_sql=_DUP_ROWS_SQL
    )
    assert cortex.asks
    assert_envelope_valid(env)
    assert env["abstained"] is True, env.get("text")
    assert env["badge"] == "ABSTAIN"
    assert env.get("rows") in (None, [])
    assert not probed, f"probe ran on SQL the grant function rejects: {probed}"
    assert name not in _visible(env), "an ungranted name reached the viewer"


def test_cloop_b_runs_a_granted_warehouse_alias_on_the_lake_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The checker accepts warehouse_<granted table>; the loop's own run must too."""
    db = tmp_path / "cloop_alias.duckdb"
    ensure_demo_warehouse(db)
    aliased = (
        "SELECT l.location_id AS location_id, COUNT(i.sku) AS sku_count "
        "FROM warehouse_locations l JOIN warehouse_inventory i "
        "ON i.location_id = l.location_id GROUP BY l.location_id"
    )
    env, _ = _prod_post(
        db,
        aliased,
        monkeypatch,
        minter,
        route="cloop_b",
        pass_warehouse=True,
        rows_sql=_SKU_PER_LOCATION,
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert "db_error" not in _visible(env)
    assert _cells(env["rows"]) == _cells(_executed(db, _SKU_PER_LOCATION))


def test_recursive_cte_cannot_hide_an_ungranted_table_in_its_first_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Finance does not grant alerts. A CTE that shares its name must not open it."""
    db = tmp_path / "rec_shadow.duckdb"
    ensure_demo_warehouse(db)
    probed = _spy_probes(monkeypatch)
    env, cortex = _prod_post(
        db,
        _RECURSIVE_SHADOW,
        monkeypatch,
        minter,
        pass_warehouse=True,
        rows_sql="SELECT 1 AS a",
    )
    assert cortex.asks
    assert_envelope_valid(env)
    assert env["abstained"] is True, env.get("text")
    assert env["badge"] == "ABSTAIN"
    assert env.get("rows") in (None, [])
    assert not probed
    assert "alerts" not in _visible(env)


@pytest.mark.parametrize("route", _ROUTES)
def test_recursive_date_spine_over_granted_tables_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    db = tmp_path / "rec_spine.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, _RECURSIVE_SPINE, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["abstained"] is False, (route, env.get("text"))
    assert env["badge"] == "L2_VALIDATED", (route, env.get("assumptions"))
    expected = _executed(db, _RECURSIVE_SPINE)
    assert len(env["rows"]) == len(expected) == 5, route
    assert [r["n"] for r in env["rows"]] == [r["n"] for r in expected], route


# A join inside a CTE or derived table, or a join to a CTE, fans out the rows
# the outer query aggregates. The same query without the CTE abstains.
_CTE_FANOUT = (
    (
        "join_to_cte_sum",
        "WITH t AS (SELECT sku FROM transactions) "
        "SELECT SUM(i.quantity_kg) AS kg FROM inventory i JOIN t ON t.sku = i.sku",
    ),
    (
        "cross_join_cte_sum",
        "WITH l AS (SELECT location_id FROM locations) "
        "SELECT SUM(i.quantity_kg) AS kg FROM inventory i JOIN l ON TRUE",
    ),
    (
        "join_inside_cte",
        "WITH j AS (SELECT i.sku AS sku FROM inventory i JOIN locations l ON TRUE) "
        "SELECT sku FROM j",
    ),
    (
        "count_per_key_over_cte_join",
        "WITH l AS (SELECT location_id FROM locations) "
        "SELECT i.sku AS sku, COUNT(*) AS n FROM inventory i JOIN l ON TRUE GROUP BY i.sku",
    ),
    (
        "sum_over_joining_cte",
        "WITH j AS (SELECT i.quantity_kg AS q FROM inventory i JOIN locations l ON TRUE) "
        "SELECT SUM(q) AS kg FROM j",
    ),
    (
        "sum_over_joining_derived_table",
        "SELECT SUM(s.q) AS kg FROM "
        "(SELECT i.quantity_kg AS q FROM inventory i JOIN locations l ON TRUE) s",
    ),
)


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("case", _CTE_FANOUT, ids=[c[0] for c in _CTE_FANOUT])
def test_fanout_through_a_cte_never_takes_l2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    case: tuple[str, str],
) -> None:
    """The grain checks also run on the query with its CTEs inlined."""
    _, sql = case
    db = tmp_path / "cte_fan2.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (route, env["badge"])
    assert env["abstained"] is True
    assert env.get("rows") in (None, [])


_CTE_ONE_TO_ONE = (
    (
        "aggregate_cte_joined_one_to_one",
        "WITH c AS (SELECT sku, COUNT(*) AS n FROM transactions GROUP BY sku) "
        "SELECT i.sku AS sku, i.quantity_kg AS kg, c.n AS n FROM inventory i "
        "JOIN c ON c.sku = i.sku",
    ),
    (
        "transactions_per_category_through_cte",
        "WITH j AS (SELECT t.txn_id AS txn_id, i.category AS category FROM transactions t "
        "JOIN inventory i ON i.sku = t.sku) "
        "SELECT category, COUNT(*) AS n FROM j GROUP BY category",
    ),
)


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("case", _CTE_ONE_TO_ONE, ids=[c[0] for c in _CTE_ONE_TO_ONE])
def test_a_cte_join_that_keeps_the_grain_keeps_l2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    case: tuple[str, str],
) -> None:
    """R-0005: inlining a CTE checks the grain; it does not refuse every CTE join."""
    _, sql = case
    db = tmp_path / "cte_one.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED", (route, env.get("text"))

    def _key(row: dict[str, Any]) -> str:
        return repr(sorted(row.items()))

    assert sorted(env["rows"], key=_key) == sorted(_executed(db, sql), key=_key)


_LATERAL_READERS = (
    (
        "query_table",
        "SELECT c.alert_id AS alert_id FROM inventory i "
        "JOIN LATERAL query_table('alerts') c ON TRUE WHERE i.sku = 'RS622XK'",
    ),
    (
        "duckdb_settings",
        "SELECT c.name AS name, c.value AS value FROM inventory i, LATERAL duckdb_settings() c "
        "WHERE i.sku = 'RS622XK'",
    ),
    (
        "pragma_table_info",
        "SELECT c.name AS name FROM inventory i CROSS JOIN LATERAL pragma_table_info('alerts') c",
    ),
)


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("case", _LATERAL_READERS, ids=[c[0] for c in _LATERAL_READERS])
def test_a_table_function_through_lateral_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
    case: tuple[str, str],
) -> None:
    _, sql = case
    db = tmp_path / "lateral.duckdb"
    ensure_demo_warehouse(db)
    probed = _spy_probes(monkeypatch)
    env, _ = _prod_post(
        db, sql, monkeypatch, minter, route=route, pass_warehouse=True, rows_sql=_DUP_ROWS_SQL
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", route
    assert env.get("rows") in (None, [])
    assert not probed, f"{route} probed a table function: {probed}"


def test_lateral_generators_and_subqueries_are_not_readers() -> None:
    from dms_executor.generative_ask import _reads_files

    granted = {"inventory", "transactions"}
    assert not _reads_files(
        "SELECT i.sku, u.x FROM inventory i, LATERAL unnest([i.quantity_kg]) u(x)", granted
    )
    assert not _reads_files(
        "SELECT i.sku, g.n FROM inventory i CROSS JOIN LATERAL generate_series(1, 3) g(n)",
        granted,
    )
    assert not _reads_files(
        "SELECT i.sku, t.n FROM inventory i, LATERAL (SELECT COUNT(*) AS n FROM transactions t "
        "WHERE t.sku = i.sku) t",
        granted,
    )


def test_a_recursive_middle_arm_reads_the_real_table() -> None:
    """DuckDB's base term is everything left of the last UNION."""
    from dms_executor.generative_ask import cited_relations

    sql = (
        "WITH RECURSIVE alerts(n) AS (SELECT 1 UNION ALL SELECT length(message) FROM alerts "
        "UNION ALL SELECT n + 1 FROM alerts WHERE n < 2) SELECT n FROM alerts ORDER BY n"
    )
    assert cited_relations(sql) == {"alerts"}
    nested = (
        "WITH a AS (WITH a AS (SELECT * FROM inventory) SELECT * FROM a) SELECT * FROM a"
    )
    assert cited_relations(nested) == {"inventory"}


@pytest.mark.parametrize("route", _ROUTES)
def test_a_recursive_middle_arm_over_an_ungranted_table_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    sql = (
        "WITH RECURSIVE alerts(n) AS (SELECT 1 UNION ALL SELECT length(message) FROM alerts "
        "UNION ALL SELECT n + 1 FROM alerts WHERE n < 2) SELECT n FROM alerts ORDER BY n"
    )
    db = tmp_path / "rec3.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", route
    assert env.get("rows") in (None, [])


def test_a_warehouse_alias_used_as_a_column_qualifier_runs_on_the_lake_name() -> None:
    from dms_executor.served_gate import lake_alias_sql

    assert lake_alias_sql("SELECT warehouse_inventory.sku AS sku FROM warehouse_inventory") == (
        "SELECT inventory.sku AS sku FROM inventory"
    )
    assert lake_alias_sql("SELECT sku FROM main.warehouse_inventory") == (
        "SELECT sku FROM main.inventory"
    )
    assert lake_alias_sql("SELECT w.sku FROM warehouse_inventory w") == (
        "SELECT w.sku FROM inventory AS w"
    )


@pytest.mark.parametrize("route", _ROUTES)
def test_a_qualified_warehouse_alias_keeps_l2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    sql = (
        "SELECT warehouse_inventory.category AS category, "
        "SUM(warehouse_inventory.quantity_kg) AS kg FROM warehouse_inventory "
        "GROUP BY warehouse_inventory.category"
    )
    rows_sql = (
        "SELECT inventory.category AS category, SUM(inventory.quantity_kg) AS kg "
        "FROM inventory GROUP BY inventory.category"
    )
    db = tmp_path / "alias_q.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(
        db, sql, monkeypatch, minter, route=route, pass_warehouse=True, rows_sql=rows_sql
    )
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED", (route, env.get("text"))


@pytest.mark.parametrize("badge", ["l2_anomalous", "certified", "governed_metric"])
def test_rejected_sql_takes_no_confident_badge_of_any_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, badge: str
) -> None:
    sql = "SELECT sku, nonexistent_col FROM inventory"
    db = tmp_path / "badge.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(
        db, sql, monkeypatch, minter, pass_warehouse=True, rows_sql=_DUP_ROWS_SQL, badge=badge
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (badge, env["badge"])
    assert env.get("rows") in (None, [])


# Round 3: the grain check reads each base table's rowid, so it needs no
# ontology, checks every aggregating SELECT where it stands, and counts a row
# against a table only when it repeats within the same group.
_SPINE = (
    "WITH days AS (SELECT CAST(d AS DATE) AS dy FROM generate_series(DATE '2026-07-01', "
    "DATE '2026-07-25', INTERVAL 1 DAY) g(d)) "
)
_GATE_FANOUT = {
    "aggregate_cte": (
        "WITH g AS (SELECT i.category AS category, SUM(i.quantity_kg) AS kg FROM inventory i "
        "JOIN transactions t ON t.sku = i.sku GROUP BY i.category) SELECT category, kg FROM g"
    ),
    "aggregate_derived": (
        "SELECT category, kg FROM (SELECT i.category AS category, SUM(i.quantity_kg) AS kg "
        "FROM inventory i JOIN transactions t ON t.sku = i.sku GROUP BY i.category) x"
    ),
    "limit_body": (
        "WITH j AS (SELECT i.sku, i.quantity_kg AS q FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku ORDER BY i.sku LIMIT 100) SELECT SUM(q) FROM j"
    ),
    "union_body": (
        "WITH j AS (SELECT i.quantity_kg AS q FROM inventory i JOIN locations l ON TRUE "
        "UNION ALL SELECT 1.0) SELECT SUM(q) FROM j"
    ),
    "count_of_the_repeated_table": (
        "SELECT i.category, COUNT(i.sku) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "top_level_union_arm": (
        "SELECT SUM(i.quantity_kg) FROM inventory i JOIN transactions t ON t.sku = i.sku "
        "UNION ALL SELECT SUM(quantity_kg) FROM inventory"
    ),
    "scalar_subquery": (
        "SELECT sku, (SELECT SUM(i.quantity_kg) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku) AS tot FROM inventory"
    ),
    "count_over_a_cross_join": (
        "SELECT f.sku AS sku, COUNT(*) AS n FROM inventory f JOIN locations l ON TRUE "
        "GROUP BY f.sku"
    ),
    "count_over_a_comma_join": (
        "SELECT f.sku, COUNT(*) FROM inventory f, locations l GROUP BY f.sku"
    ),
    "having_over_fanout": (
        "SELECT i.category FROM inventory i JOIN transactions t ON t.sku = i.sku "
        "GROUP BY i.category HAVING SUM(i.quantity_kg) > 3000"
    ),
    "order_by_over_fanout": (
        "SELECT i.category FROM inventory i JOIN transactions t ON t.sku = i.sku "
        "GROUP BY i.category ORDER BY SUM(i.unit_cost_myr) DESC LIMIT 1"
    ),
    "aggregate_cte_repeated_by_a_join": (
        "WITH ct AS (SELECT category, SUM(quantity_kg) AS tot FROM inventory GROUP BY category) "
        "SELECT i.category, SUM(c.tot) FROM inventory i JOIN ct c ON c.category = i.category "
        "GROUP BY i.category"
    ),
    "aggregate_derived_repeated_by_a_join": (
        "SELECT i.category, SUM(c.tot) FROM inventory i JOIN (SELECT category, "
        "SUM(quantity_kg) AS tot FROM inventory GROUP BY category) c "
        "ON c.category = i.category GROUP BY i.category"
    ),
    "sum_distinct_over_fanout": (
        "SELECT i.category, SUM(DISTINCT i.quantity_kg) FROM inventory i "
        "JOIN transactions t ON t.sku = i.sku GROUP BY i.category"
    ),
    "distinct_body_finer_than_the_measure": (
        "WITH j AS (SELECT DISTINCT i.sku, i.quantity_kg q, t.txn_type FROM inventory i "
        "JOIN transactions t ON t.sku = i.sku) SELECT SUM(q) FROM j"
    ),
    "qualify_body_finer_than_the_measure": (
        "WITH j AS (SELECT i.sku, i.quantity_kg q FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku QUALIFY ROW_NUMBER() OVER (PARTITION BY i.sku, t.txn_type "
        "ORDER BY t.ts) = 1) SELECT SUM(q) FROM j"
    ),
    "natural_join_of_two_derived_tables": (
        "SELECT SUM(x.q) FROM (SELECT sku, quantity_kg q FROM inventory) x "
        "NATURAL JOIN (SELECT sku FROM transactions) y"
    ),
    "median_over_fanout": (
        "SELECT i.category, MEDIAN(i.unit_cost_myr) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "string_agg_over_fanout": (
        "SELECT i.category, STRING_AGG(i.sku, ',') FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "list_over_fanout": (
        "SELECT i.category, LIST(i.sku) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "mode_over_fanout": (
        "SELECT i.category, MODE(i.unit_cost_myr) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "rank_in_the_body_of_a_fanout": (
        "WITH ranked AS (SELECT i.category AS category, i.quantity_kg AS kg, RANK() OVER "
        "(PARTITION BY i.category ORDER BY i.quantity_kg DESC) AS rk FROM inventory i "
        "JOIN transactions t ON t.sku = i.sku) SELECT category, SUM(kg) FROM ranked "
        "WHERE rk <= 2 GROUP BY category"
    ),
    "windowed_sum_in_the_body_of_a_fanout": (
        "WITH w AS (SELECT i.category, i.quantity_kg AS kg, SUM(t.quantity_kg) OVER "
        "(PARTITION BY i.sku) AS moved FROM inventory i JOIN transactions t ON t.sku = i.sku) "
        "SELECT category, SUM(kg) FROM w GROUP BY category"
    ),
    "mean_over_fanout": (
        "SELECT i.category, MEAN(i.quantity_kg) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "three_cte_chain": (
        "WITH a AS (SELECT sku, quantity_kg FROM inventory), b AS (SELECT a.quantity_kg "
        "FROM a JOIN transactions t ON t.sku = a.sku), c AS (SELECT * FROM b) "
        "SELECT SUM(quantity_kg) FROM c"
    ),
}
_GATE_GRAIN_KEPT = {
    "many_side_cte_count": (
        "WITH recent AS (SELECT * FROM transactions WHERE ts >= TIMESTAMP '2026-07-01') "
        "SELECT i.category, COUNT(*) FROM recent r JOIN inventory i ON i.sku = r.sku "
        "GROUP BY i.category"
    ),
    "many_side_derived_count": (
        "SELECT i.category, COUNT(*) FROM (SELECT * FROM transactions) r "
        "JOIN inventory i ON i.sku = r.sku GROUP BY i.category"
    ),
    "transactions_per_category": (
        "SELECT i.category, COUNT(*) AS n FROM inventory i JOIN transactions t "
        "ON i.sku = t.sku GROUP BY i.category"
    ),
    "revenue_per_transaction": (
        "SELECT i.category, SUM(t.quantity_kg * i.unit_cost_myr) FROM transactions t "
        "JOIN inventory i ON i.sku = t.sku GROUP BY i.category"
    ),
    "spine_sum_with_empty_days": (
        _SPINE + "SELECT days.dy, COALESCE(SUM(t.quantity_kg), 0) FROM days "
        "LEFT JOIN transactions t ON CAST(t.ts AS DATE) = days.dy GROUP BY days.dy"
    ),
    "cumulative_count": (
        _SPINE + "SELECT days.dy, COUNT(t.txn_id) FROM days "
        "LEFT JOIN transactions t ON CAST(t.ts AS DATE) <= days.dy GROUP BY days.dy"
    ),
    "cumulative_sum": (
        _SPINE + "SELECT days.dy, SUM(t.quantity_kg) FROM days "
        "LEFT JOIN transactions t ON CAST(t.ts AS DATE) <= days.dy GROUP BY days.dy"
    ),
    "comma_join_linked_in_where": (
        "SELECT i.category, COUNT(*) FROM transactions t, inventory i "
        "WHERE i.sku = t.sku GROUP BY i.category"
    ),
    "sum_over_an_aggregate_cte": (
        "WITH c AS (SELECT sku, SUM(quantity_kg) AS q FROM transactions GROUP BY sku) "
        "SELECT SUM(c.q) FROM c JOIN inventory i ON i.sku = c.sku"
    ),
    "top_n_per_group_window": (
        "WITH r AS (SELECT i.category, t.txn_id, t.quantity_kg, ROW_NUMBER() OVER "
        "(PARTITION BY i.category ORDER BY t.quantity_kg DESC) rn FROM transactions t "
        "JOIN inventory i ON i.sku = t.sku) SELECT category, SUM(quantity_kg) FROM r "
        "WHERE rn <= 3 GROUP BY category"
    ),
    "group_by_ordinal": (
        "SELECT i.category, COUNT(*) FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku GROUP BY 1"
    ),
    "group_by_all": (
        "SELECT i.category, COUNT(*) FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku GROUP BY ALL"
    ),
    "count_distinct_through_a_fanout": (
        "SELECT i.category, COUNT(DISTINCT i.sku) FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku GROUP BY i.category"
    ),
    "count_distinct_two_hops": (
        "SELECT l.name, COUNT(DISTINCT i.sku) FROM locations l JOIN inventory i "
        "ON i.location_id = l.location_id JOIN transactions t ON t.sku = i.sku GROUP BY l.name"
    ),
    "many_side_aggregate_cte_count": (
        "WITH d AS (SELECT sku, CAST(ts AS DATE) dy, SUM(quantity_kg) q FROM transactions "
        "GROUP BY sku, CAST(ts AS DATE)) SELECT i.category, COUNT(*) FROM d "
        "JOIN inventory i ON i.sku = d.sku GROUP BY i.category"
    ),
    "many_side_limit_cte_count": (
        "WITH top AS (SELECT * FROM transactions ORDER BY ts DESC LIMIT 6) SELECT i.category, "
        "COUNT(*) FROM top JOIN inventory i ON i.sku = top.sku GROUP BY i.category"
    ),
    "many_side_union_cte_count": (
        "WITH u AS (SELECT sku FROM transactions WHERE txn_type = 'IN' UNION ALL SELECT sku "
        "FROM transactions WHERE txn_type <> 'IN') SELECT i.category, COUNT(*) FROM u "
        "JOIN inventory i ON i.sku = u.sku GROUP BY i.category"
    ),
    "rolling_window_spine": (
        "WITH days AS (SELECT CAST(d AS DATE) AS day FROM generate_series(DATE '2026-07-01', "
        "DATE '2026-07-25', INTERVAL 1 DAY) g(d)) SELECT d.day, COUNT(*) FROM days d "
        "JOIN transactions t ON t.ts >= d.day AND t.ts <= d.day + INTERVAL 7 DAY GROUP BY d.day"
    ),
    "qualify_dedup_body": (
        "WITH latest AS (SELECT t.sku, t.quantity_kg FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku QUALIFY ROW_NUMBER() OVER (PARTITION BY t.sku ORDER BY t.ts DESC) = 1) "
        "SELECT SUM(quantity_kg) FROM latest"
    ),
    "distinct_body": (
        "WITH s AS (SELECT DISTINCT i.sku, i.quantity_kg FROM inventory i JOIN transactions t "
        "ON t.sku = i.sku) SELECT SUM(quantity_kg) FROM s"
    ),
    "in_list_join_condition": (
        "WITH c AS (SELECT 'RAW' a, 'PARTS' b) SELECT COUNT(*) FROM inventory i "
        "JOIN c ON i.category IN (c.a, c.b)"
    ),
    "lookup_left_join_total": (
        "SELECT COUNT(*) FROM inventory i LEFT JOIN suppliers s ON s.supplier_id = i.supplier_id"
    ),
    "grouping_sets": (
        "SELECT i.category, l.name, SUM(t.quantity_kg) FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku JOIN locations l ON l.location_id = t.location_id "
        "GROUP BY GROUPING SETS ((i.category), (l.name), ())"
    ),
    "rollup": (
        "SELECT i.category, l.name, SUM(t.quantity_kg) FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku JOIN locations l ON l.location_id = t.location_id "
        "GROUP BY ROLLUP (i.category, l.name)"
    ),
    "percent_of_total_cross_join_to_a_total": (
        "WITH tot AS (SELECT COUNT(*) AS n FROM transactions) SELECT i.category, "
        "COUNT(*) * 100.0 / MAX(tot.n) AS pct FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku CROSS JOIN tot GROUP BY i.category"
    ),
    "count_cross_join_to_a_parameter_cte": (
        "WITH p AS (SELECT DATE '2026-07-01' AS start_d) SELECT i.category, COUNT(*) "
        "FROM transactions t JOIN inventory i ON i.sku = t.sku CROSS JOIN p "
        "WHERE t.ts >= p.start_d GROUP BY i.category"
    ),
    "count_distinct_aggregate_stays_exempt": (
        "SELECT i.category, COUNT(DISTINCT i.sku), MAX(t.quantity_kg) FROM inventory i "
        "JOIN transactions t ON t.sku = i.sku GROUP BY i.category"
    ),
    "group_by_output_alias": (
        "SELECT i.category AS cat, COUNT(*) FROM transactions t JOIN inventory i "
        "ON i.sku = t.sku GROUP BY cat"
    ),
}


@pytest.fixture(scope="module")
def gate_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("gate") / "gate.duckdb"
    ensure_demo_warehouse(db)
    return db


@pytest.mark.parametrize("name", sorted(_GATE_FANOUT))
def test_the_grain_check_refuses_fanout_wherever_the_join_sits(
    gate_lake: Path, name: str
) -> None:
    from dms_executor.served_gate import served_result_reason

    why = served_result_reason(
        _GATE_FANOUT[name], warehouse=gate_lake, as_of="2026-10-16T00:00:00Z"
    )
    assert why == "fanout_subject_keys", name


@pytest.mark.parametrize("name", sorted(_GATE_GRAIN_KEPT))
def test_the_grain_check_keeps_joins_at_a_tables_grain(gate_lake: Path, name: str) -> None:
    """R-0005: a one-to-many join counted or summed on its many side is served."""
    from dms_executor.served_gate import served_result_reason

    why = served_result_reason(
        _GATE_GRAIN_KEPT[name], warehouse=gate_lake, as_of="2026-10-16T00:00:00Z"
    )
    assert why is None, name


def test_an_outer_join_row_with_no_match_is_not_fanout(tmp_path: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    db = tmp_path / "left.duckdb"
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        con.execute(
            "INSERT INTO suppliers SELECT * REPLACE ('SUP-NONE' AS supplier_id, "
            "'Nobody Ltd' AS supplier_name) FROM suppliers LIMIT 1"
        )
    finally:
        con.close()
    for agg in ("SUM(i.quantity_kg)", "AVG(i.quantity_kg)", "COUNT(i.sku)"):
        sql = (
            f"SELECT s.supplier_name, {agg} FROM suppliers s LEFT JOIN inventory i "
            "ON i.supplier_id = s.supplier_id GROUP BY s.supplier_name"
        )
        assert served_result_reason(sql, warehouse=db, as_of="2026-10-16T00:00:00Z") is None


@pytest.mark.parametrize("route", _ROUTES)
def test_a_lake_the_ontology_does_not_verify_is_still_grain_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    """One duplicate lot breaks the demo ontology; every route still serves and still checks."""
    db = tmp_path / "unverified.duckdb"
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        con.execute("INSERT INTO inventory SELECT * FROM inventory LIMIT 1")
    finally:
        con.close()
    assert load_verified_ontology(db) is None
    listing = "SELECT sku, quantity_kg FROM inventory"
    env, _ = _prod_post(db, listing, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED", (route, env.get("text"))
    fanned = "SELECT SUM(i.quantity_kg) AS kg FROM inventory i JOIN transactions t ON t.sku = i.sku"
    env, _ = _prod_post(db, fanned, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (route, env["badge"])


@pytest.mark.parametrize("route", _ROUTES)
def test_a_non_mergeable_cte_fanout_never_takes_l2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    db = tmp_path / "agg_cte.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(
        db, _GATE_FANOUT["aggregate_cte"], monkeypatch, minter, route=route, pass_warehouse=True
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (route, env["badge"])
    assert env.get("rows") in (None, [])


@pytest.mark.parametrize("route", _ROUTES)
def test_a_many_side_cte_count_keeps_l2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    db = tmp_path / "many_cte.duckdb"
    ensure_demo_warehouse(db)
    sql = _GATE_GRAIN_KEPT["many_side_cte_count"]
    env, _ = _prod_post(db, sql, monkeypatch, minter, route=route, pass_warehouse=True)
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED", (route, env.get("text"))


_NOT_RECURSIVE = (
    "WITH RECURSIVE alerts(message) AS (SELECT 'x' AS message UNION BY NAME "
    "SELECT message FROM alerts) SELECT message FROM alerts",
    "WITH RECURSIVE alerts(n) AS (SELECT 1 EXCEPT SELECT length(message) FROM alerts) "
    "SELECT n FROM alerts",
    "WITH RECURSIVE alerts(s) AS (SELECT 'HIGH' INTERSECT SELECT severity FROM alerts) "
    "SELECT s FROM alerts",
)


@pytest.mark.parametrize("sql", _NOT_RECURSIVE, ids=["union_by_name", "except", "intersect"])
def test_only_a_plain_union_recurses(sql: str) -> None:
    """DuckDB binds the CTE's own name to the table under EXCEPT, INTERSECT, UNION BY NAME."""
    from dms_executor.generative_ask import cited_relations

    assert "alerts" in cited_relations(sql)


@pytest.mark.parametrize("route", _ROUTES)
def test_a_union_by_name_body_does_not_read_an_ungranted_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, route: str
) -> None:
    db = tmp_path / "by_name.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(
        db, _NOT_RECURSIVE[0], monkeypatch, minter, route=route, pass_warehouse=True
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", (route, env["badge"])
    assert env.get("rows") in (None, [])


def test_a_three_part_alias_qualifier_runs_on_the_lake_name() -> None:
    from dms_executor.served_gate import lake_alias_sql

    assert lake_alias_sql(
        "SELECT main.warehouse_inventory.sku AS sku FROM main.warehouse_inventory"
    ) == "SELECT main.inventory.sku AS sku FROM main.inventory"


def test_count_star_over_an_outer_join_with_childless_parents_is_refused(tmp_path: Path) -> None:
    """Each parent with no child counts as 1; COUNT(child) is the right count and is served."""
    from dms_executor.served_gate import served_result_reason

    db = tmp_path / "orphan.duckdb"
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        con.execute(
            "INSERT INTO locations SELECT * REPLACE ('LOC-NONE' AS location_id, "
            "'Empty Yard' AS name) FROM locations LIMIT 1"
        )
    finally:
        con.close()
    star = (
        "SELECT l.name, COUNT(*) AS lots FROM locations l LEFT JOIN inventory i "
        "ON i.location_id = l.location_id GROUP BY l.name"
    )
    child = star.replace("COUNT(*)", "COUNT(i.sku)")
    as_of = "2026-10-16T00:00:00Z"
    assert served_result_reason(star, warehouse=db, as_of=as_of) == "outer_join_count"
    assert served_result_reason(child, warehouse=db, as_of=as_of) is None


def test_a_probe_that_cannot_run_does_not_pass(
    gate_lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_executor.demo_warehouse as warehouse
    from dms_executor.served_gate import served_result_reason

    def refuse(_path: Path) -> Any:
        raise RuntimeError("lake busy")

    monkeypatch.setattr(warehouse, "connect_serving", refuse)
    sql = _GATE_GRAIN_KEPT["transactions_per_category"]
    assert served_result_reason(sql, warehouse=gate_lake, as_of="2026-10-16T00:00:00Z") == (
        "grain_unprobed"
    )


def test_probes_do_not_take_the_ingest_write_lease(
    gate_lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe read must not wait behind serving reads, nor block them."""
    import dms_executor.demo_warehouse as warehouse
    from dms_executor.served_gate import served_result_reason

    def no_writes(_path: Path, **_kwargs: Any) -> Any:
        raise AssertionError("a probe opened the ingest connection")

    monkeypatch.setattr(warehouse, "connect_file", no_writes)
    as_of = "2026-10-16T00:00:00Z"
    sql = _GATE_FANOUT["aggregate_cte"]
    assert served_result_reason(sql, warehouse=gate_lake, as_of=as_of) == "fanout_subject_keys"


def test_a_warehouse_schema_qualified_table_runs_on_the_lake_name() -> None:
    """Cortex cites "warehouse"."inventory"; grant and EXPLAIN both read it as inventory."""
    from dms_executor.served_gate import lake_alias_sql

    assert lake_alias_sql(
        'SELECT i.sku FROM "warehouse"."inventory" AS i JOIN warehouse.suppliers AS s '
        "ON i.supplier_id = s.supplier_id"
    ) == 'SELECT i.sku FROM "inventory" AS i JOIN suppliers AS s ON i.supplier_id = s.supplier_id'
    assert lake_alias_sql("SELECT warehouse.inventory.sku FROM warehouse.inventory") == (
        "SELECT inventory.sku FROM inventory"
    )


def _lake_with(tmp_path: Path, name: str, *statements: str) -> Path:
    db = tmp_path / name
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()
    return db


def test_childless_parents_are_not_counted_as_one_child(tmp_path: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    db = _lake_with(
        tmp_path,
        "childless.duckdb",
        "INSERT INTO locations SELECT * REPLACE ('LOC-NONE' AS location_id, "
        "'Empty Yard' AS name) FROM locations LIMIT 1",
    )
    as_of = "2026-10-16T00:00:00Z"
    refused = (
        "SELECT l.name, COUNT(*) FROM locations l LEFT JOIN inventory i "
        "ON i.location_id = l.location_id GROUP BY l.name",
        "SELECT l.name, COUNT(*) FROM inventory i RIGHT JOIN locations l "
        "ON i.location_id = l.location_id GROUP BY l.name",
        "SELECT l.name, COUNT(1) FROM locations l LEFT JOIN inventory i "
        "ON i.location_id = l.location_id GROUP BY l.name",
        _SPINE + "SELECT days.dy, COUNT(*) FROM days LEFT JOIN transactions t "
        "ON CAST(t.ts AS DATE) = days.dy GROUP BY days.dy",
    )
    for sql in refused:
        assert served_result_reason(sql, warehouse=db, as_of=as_of) == "outer_join_count", sql
    served = (
        "SELECT l.name, COUNT(i.sku) FROM locations l LEFT JOIN inventory i "
        "ON i.location_id = l.location_id GROUP BY l.name",
        "SELECT l.name, COUNT(*) FILTER (WHERE i.sku IS NOT NULL) FROM locations l "
        "LEFT JOIN inventory i ON i.location_id = l.location_id GROUP BY l.name",
    )
    for sql in served:
        assert served_result_reason(sql, warehouse=db, as_of=as_of) is None, sql


def test_lookup_outer_joins_with_orphan_keys_keep_their_count(tmp_path: Path) -> None:
    """Each fact row is counted once whether or not its lookup key resolves."""
    from dms_executor.served_gate import served_result_reason

    db = _lake_with(
        tmp_path,
        "orphans.duckdb",
        "INSERT INTO inventory SELECT * REPLACE ('SKU-ORPH' AS sku, 'SUP-99' AS supplier_id) "
        "FROM inventory LIMIT 1",
    )
    as_of = "2026-10-16T00:00:00Z"
    for sql in (
        "SELECT i.category, COUNT(*) FROM inventory i LEFT JOIN suppliers s "
        "ON s.supplier_id = i.supplier_id GROUP BY i.category",
        "SELECT t.txn_type, COUNT(*) FROM inventory i RIGHT JOIN transactions t "
        "ON t.sku = i.sku GROUP BY t.txn_type",
    ):
        assert served_result_reason(sql, warehouse=db, as_of=as_of) is None, sql


def test_uploaded_tables_are_grain_checked_with_bare_columns(tmp_path: Path) -> None:
    """bronze.* is qualified against the lake catalog; a bare measure is attributed."""
    from dms_executor.served_gate import served_result_reason

    db = _lake_with(
        tmp_path,
        "uploads.duckdb",
        "CREATE SCHEMA bronze",
        "CREATE TABLE bronze.sales (sale_id INTEGER, cust VARCHAR, amount DOUBLE)",
        "INSERT INTO bronze.sales VALUES (1, 'C0', 100), (2, 'C1', 200), (3, 'C2', 300)",
        "CREATE TABLE bronze.cust_dim (cust VARCHAR, region VARCHAR)",
        "INSERT INTO bronze.cust_dim VALUES ('C0', 'N'), ('C0', 'S'), ('C1', 'N'), ('C2', 'S')",
    )
    as_of = "2026-10-16T00:00:00Z"
    for sql in (
        "SELECT SUM(amount) FROM bronze.sales s JOIN bronze.cust_dim d ON d.cust = s.cust",
        "SELECT s.cust, SUM(amount) FROM bronze.sales s JOIN bronze.cust_dim d "
        "ON d.cust = s.cust GROUP BY s.cust",
    ):
        assert served_result_reason(sql, warehouse=db, as_of=as_of) == "fanout_subject_keys", sql
    one_to_one = (
        "SELECT d.region, SUM(amount) FROM bronze.sales s JOIN bronze.cust_dim d "
        "ON d.cust = s.cust AND d.region = 'N' GROUP BY d.region"
    )
    assert served_result_reason(one_to_one, warehouse=db, as_of=as_of) is None


def test_a_cte_name_folds_case_as_duckdb_does() -> None:
    """U+212A (Kelvin sign) lowercases to k in Python, not in DuckDB: stock stays a table."""
    from dms_executor.generative_ask import cited_relations

    sql = 'WITH "stoc\u212a" AS (SELECT 1 AS x) SELECT s.secret FROM stock s, inventory i'
    assert "stock" in cited_relations(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH inventory AS (TABLE alerts) SELECT * FROM inventory",
        "SELECT * FROM inventory WHERE EXISTS ((TABLE alerts))",
    ],
)
def test_the_table_shorthand_never_reaches_the_grant(sql: str, tmp_path: Path) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "table_kw.duckdb"
    ensure_demo_warehouse(db)
    why = validate_compiled_sql(sql, grantable=set(DEMO_TABLES) - {"alerts"}, warehouse=db)
    assert why is not None and why.startswith("hostile_sql:"), why



@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sku, category, location_id, supplier_id, category FROM inventory "
        "UNION ALL TABLE alerts",
        "SELECT sku FROM inventory WHERE EXISTS (TABLE alerts)",
        "SELECT sku FROM inventory WHERE EXISTS (PIVOT alerts ON severity USING COUNT(*))",
    ],
)
def test_sql_the_parser_cannot_read_is_not_granted_on_a_guess(sql: str, tmp_path: Path) -> None:
    """sqlglot cannot read these; the regex fallback never saw alerts. Refused, not granted.

    DuckDB splits a parenthesised PIVOT into several statements, so the
    one-SELECT check (#447) refuses that one before EXPLAIN.
    """
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "unparsed.duckdb"
    ensure_demo_warehouse(db)
    why = validate_compiled_sql(sql, grantable={"inventory"}, warehouse=db)
    assert why is not None, sql
    assert why.startswith("hostile_sql:") or why == "multi_statement", why


def test_an_engine_syntax_error_still_reaches_the_repair_loop(tmp_path: Path) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "syntax.duckdb"
    ensure_demo_warehouse(db)
    why = validate_compiled_sql(
        "SELECT sku FROM inventory WHERE quantity_kg >", grantable={"inventory"}, warehouse=db
    )
    assert why == "explain:ParserException"


def test_count_filters_and_full_joins_over_childless_parents(tmp_path: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    db = _lake_with(
        tmp_path,
        "filters.duckdb",
        "INSERT INTO locations SELECT * REPLACE ('WH-F' AS location_id, 'Warehouse F' AS name, "
        "TRUE AS is_cold_storage) FROM locations LIMIT 1",
    )
    as_of = "2026-10-16T00:00:00Z"
    parent_filter = (
        "SELECT l.name, COUNT(*) FILTER (WHERE l.is_cold_storage) FROM locations l "
        "LEFT JOIN alerts a ON a.location_id = l.location_id GROUP BY l.name"
    )
    full_join = (
        "SELECT COALESCE(l.location_id, a.location_id) AS loc, COUNT(*) FROM locations l "
        "FULL JOIN alerts a ON a.location_id = l.location_id "
        "GROUP BY COALESCE(l.location_id, a.location_id)"
    )
    child_filter = (
        "SELECT l.name, COUNT(*) FILTER (WHERE a.severity = 'low') FROM locations l "
        "LEFT JOIN alerts a ON a.location_id = l.location_id GROUP BY l.name"
    )
    assert served_result_reason(parent_filter, warehouse=db, as_of=as_of) == "outer_join_count"
    assert served_result_reason(full_join, warehouse=db, as_of=as_of) == "outer_join_count"
    assert served_result_reason(child_filter, warehouse=db, as_of=as_of) is None


def test_a_lineage_too_deep_to_trace_does_not_pass(gate_lake: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    hops = ", ".join(
        f"c{n} AS (SELECT * FROM {'j' if n == 1 else f'c{n - 1}'} ORDER BY 1 LIMIT 100000)"
        for n in range(1, 6)
    )
    sql = (
        "WITH j AS (SELECT i.category, i.reorder_level_kg AS rl FROM inventory i "
        f"JOIN transactions t ON t.sku = i.sku), {hops} SELECT SUM(rl) FROM c5"
    )
    why = served_result_reason(sql, warehouse=gate_lake, as_of="2026-10-16T00:00:00Z")
    assert why in {"fanout_subject_keys", "grain_unprobed"}, why


# --- Round 7: DuckDB's own plan decides what is read; value terms decide fan-out ---

_INVISIBLE = ("​", "⁠", "﻿")


@pytest.mark.parametrize("mark", _INVISIBLE, ids=["zwsp", "word_joiner", "bom"])
def test_an_invisible_character_cannot_hide_a_table_from_the_grant(
    gate_lake: Path, mark: str
) -> None:
    """DuckDB skips U+200B, U+2060 and U+FEFF; sqlglot reads them into a name."""
    from dms_executor.generative_ask import validate_compiled_sql

    for sql in (
        f"SELECT sku FROM inventory UNION ALL SELECT message FROM{mark}alerts",
        f"SELECT sku, (SELECT COUNT(*) FROM{mark}alerts) AS n FROM inventory",
        f"SELECT sku FROM inventory WHERE EXISTS (SELECT 1 FROM{mark}alerts)",
    ):
        why = validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake)
        assert why is not None and why.startswith("hostile_sql:"), (sql, why)


def test_the_engine_plan_grant_checks_what_sqlglot_cannot_see(
    gate_lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the character check off, DuckDB's own scan list still refuses the table."""
    import dms_executor.generative_ask as ga

    monkeypatch.setattr(ga, "_invisible_format_char", lambda _sql: False)
    sql = "SELECT sku FROM inventory UNION ALL SELECT message FROM​alerts"
    assert ga.validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake) == (
        "ungranted:alerts"
    )
    granted = ga.validate_compiled_sql(sql, grantable={"inventory", "alerts"}, warehouse=gate_lake)
    assert granted is None


def test_an_upload_named_like_a_lake_table_is_not_opened_by_the_lake_grant(
    tmp_path: Path,
) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(
        tmp_path,
        "collide.duckdb",
        "CREATE SCHEMA bronze",
        "CREATE TABLE bronze.inventory AS SELECT 'OTHER-SPACE' AS sku, 'X' AS category",
    )
    upload = "SELECT sku, category FROM bronze.inventory"
    assert (
        validate_compiled_sql(upload, grantable={"inventory"}, warehouse=db)
        == "ungranted:bronze.inventory"
    )
    lake = "SELECT sku, category FROM inventory"
    assert validate_compiled_sql(lake, grantable={"inventory"}, warehouse=db) is None
    assert validate_compiled_sql(lake, grantable={"warehouse_inventory"}, warehouse=db) is None
    # The upload's own qualified grant does not open the lake table either.
    assert validate_compiled_sql(lake, grantable={"bronze.inventory"}, warehouse=db) is not None


def test_a_lake_table_named_with_a_dot_is_granted_by_its_own_name(tmp_path: Path) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(tmp_path, "dotted.duckdb", 'CREATE TABLE "usd.book" (amount DOUBLE)')
    sql = 'SELECT SUM(amount) AS total FROM "usd.book"'
    assert validate_compiled_sql(sql, grantable={"usd.book"}, warehouse=db) is None
    assert validate_compiled_sql(sql, grantable={"inventory"}, warehouse=db) is not None


@pytest.mark.parametrize("route", ["flag_off", "cloop_b", "generated"])
def test_an_invisible_character_bypass_is_refused_on_every_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    route: str,
) -> None:
    db = tmp_path / "zw.duckdb"
    ensure_demo_warehouse(db)
    sql = "SELECT sku FROM inventory UNION ALL SELECT message FROM​alerts"
    env, _ = _prod_post(
        db,
        sql,
        monkeypatch,
        minter,
        route=route,
        pass_warehouse=True,
        rows_sql="SELECT sku FROM inventory UNION ALL SELECT message FROM alerts",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True, route
    assert env["badge"] == "ABSTAIN", route
    assert env.get("rows") in (None, []), route
    assert "Load near capacity" not in str(env.get("text") or ""), route


_JOIN = "FROM inventory i JOIN transactions t ON t.sku = i.sku GROUP BY i.category"

#: A parent measure the join repeats, carried through a CASE branch, an IF, a
#: flag multiplier or one term of a difference. The child column only picks
#: rows or sits in another term.
_VALUE_TERM_FANOUT = {
    "case_branch": (
        "SELECT i.category, SUM(CASE WHEN t.txn_type = 'outbound' THEN i.quantity_kg "
        f"ELSE 0 END) {_JOIN}"
    ),
    "if_branch": (f"SELECT i.category, SUM(IF(t.txn_type = 'outbound', i.quantity_kg, 0)) {_JOIN}"),
    "flag_multiplier": (
        "SELECT i.category, SUM(i.quantity_kg * CASE WHEN t.txn_type = 'outbound' "
        f"THEN 1 ELSE 0 END) {_JOIN}"
    ),
    "difference": f"SELECT i.category, SUM(i.quantity_kg - t.quantity_kg) {_JOIN}",
    "rounded_difference": (
        f"SELECT i.category, SUM(ROUND(i.quantity_kg - t.quantity_kg, 2)) {_JOIN}"
    ),
    "avg_case": (
        "SELECT i.category, AVG(CASE WHEN t.txn_type = 'outbound' THEN i.unit_cost_myr END) "
        f"{_JOIN}"
    ),
    "cte_case": (
        "WITH j AS (SELECT i.category, CASE WHEN t.txn_type = 'outbound' THEN i.quantity_kg "
        "ELSE 0 END AS kg FROM inventory i JOIN transactions t ON t.sku = i.sku) "
        "SELECT category, SUM(kg) FROM j GROUP BY category"
    ),
    "conditional_count_of_parent_rows": (
        "SELECT i.category, SUM(CASE WHEN i.quantity_kg < i.reorder_level_kg THEN 1 "
        f"ELSE 0 END) {_JOIN}"
    ),
}

#: Per transaction: the child row stays unique, the parent is a lookup.
_VALUE_TERM_KEPT = {
    "revenue": f"SELECT i.category, SUM(t.quantity_kg * i.unit_cost_myr) {_JOIN}",
    "child_measure_by_parent_flag": (
        "SELECT i.category, SUM(CASE WHEN i.quantity_kg < i.reorder_level_kg "
        f"THEN t.quantity_kg ELSE 0 END) {_JOIN}"
    ),
    "count_child_rows_by_child_flag": (
        f"SELECT i.category, COUNT(CASE WHEN t.txn_type = 'outbound' THEN 1 END) {_JOIN}"
    ),
}


@pytest.mark.parametrize("name", sorted(_VALUE_TERM_FANOUT))
def test_a_parent_measure_in_any_value_term_is_fanout(gate_lake: Path, name: str) -> None:
    from dms_executor.served_gate import served_result_reason

    why = served_result_reason(
        _VALUE_TERM_FANOUT[name], warehouse=gate_lake, as_of="2026-10-16T00:00:00Z"
    )
    assert why == "fanout_subject_keys", name


@pytest.mark.parametrize("name", sorted(_VALUE_TERM_KEPT))
def test_a_child_measure_with_parent_lookups_is_kept(gate_lake: Path, name: str) -> None:
    from dms_executor.served_gate import served_result_reason

    why = served_result_reason(
        _VALUE_TERM_KEPT[name], warehouse=gate_lake, as_of="2026-10-16T00:00:00Z"
    )
    assert why is None, name


# --- Round 8: statistics answers, the plan format, flags that carry no value ---


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM bronze.inventory",
        "SELECT MAX(quantity_kg) AS m FROM bronze.inventory",
        "SELECT MIN(quantity_kg) AS lo, MAX(quantity_kg) AS hi FROM bronze.inventory",
        "SELECT * FROM (DESCRIBE bronze.inventory)",
        "SELECT * FROM bronze.inventory LIMIT 0",
        "SELECT sku FROM inventory WHERE (SELECT COUNT(*) FROM bronze.inventory) > 0",
    ],
)
def test_a_statistics_answer_on_an_ungranted_upload_is_refused(tmp_path: Path, sql: str) -> None:
    """DuckDB answers these from table statistics, so the plan names no table."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(
        tmp_path,
        "stats.duckdb",
        "CREATE SCHEMA bronze",
        "CREATE TABLE bronze.inventory AS SELECT 'OTHER' AS sku, 1000.0 AS quantity_kg",
    )
    why = validate_compiled_sql(sql, grantable={"inventory"}, warehouse=db)
    assert why is not None and why.startswith("ungranted"), (sql, why)
    granted = validate_compiled_sql(sql, grantable={"inventory", "bronze.inventory"}, warehouse=db)
    assert granted is None or not granted.startswith("ungranted"), (sql, granted)


def test_another_schema_needs_its_own_grant(tmp_path: Path) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(
        tmp_path,
        "schemas.duckdb",
        "CREATE SCHEMA bronze",
        "CREATE SCHEMA silver",
        "CREATE TABLE bronze.sheet1 AS SELECT 1 AS n",
        "CREATE TABLE silver.sheet1 AS SELECT 2 AS n",
    )
    sql = "SELECT COUNT(*) AS c, MAX(n) AS m FROM silver.sheet1"
    assert validate_compiled_sql(sql, grantable={"bronze.sheet1"}, warehouse=db) == (
        "ungranted:silver.sheet1"
    )
    own = "SELECT COUNT(*) AS c, MAX(n) AS m FROM bronze.sheet1"
    assert validate_compiled_sql(own, grantable={"bronze.sheet1"}, warehouse=db) is None


def test_the_plan_format_this_check_reads_is_the_installed_one(gate_lake: Path) -> None:
    """The grant reads DuckDB's EXPLAIN (FORMAT JSON). A format change fails here, loudly."""
    import json

    import duckdb
    from dms_executor.demo_warehouse import connect_serving

    major, minor = (int(part) for part in duckdb.__version__.split(".")[:2])
    assert (major, minor) >= (1, 5), duckdb.__version__
    con = connect_serving(gate_lake)
    try:
        plan = json.loads(
            con.execute("EXPLAIN (FORMAT JSON) SELECT sku FROM inventory").fetchall()[0][1]
        )
        catalog = con.execute("SELECT current_database()").fetchone()[0]
    finally:
        con.close()
    tables = []
    stack: list[Any] = [plan]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            stack.extend(node.get("children") or [])
            info = node.get("extra_info") or {}
            if isinstance(info, dict) and "Table" in info:
                tables.append(info["Table"])
    assert tables == [f"{catalog}.main.inventory"], tables


@pytest.mark.parametrize(
    "extra_info",
    [{"Text": "inventory"}, {"Table": "inventory"}, {}],
    ids=["text_key", "bare_table", "no_name"],
)
def test_a_scan_the_check_cannot_name_is_not_granted(gate_lake: Path, extra_info: dict) -> None:
    """Older DuckDB plans print the table differently; that fails closed, never open."""
    import json

    from dms_executor.demo_warehouse import connect_serving
    from dms_executor.generative_ask import _plan_scan_reason

    plan = json.dumps([{"name": "SEQ_SCAN", "children": [], "extra_info": extra_info}])
    con = connect_serving(gate_lake)
    try:
        why = _plan_scan_reason(plan, con, {"inventory"})
    finally:
        con.close()
    assert why == "hostile_sql:sql_not_analyzable", why


_FLAG_FANOUT = {
    "bool_cast": (
        "SELECT i.category, SUM(i.quantity_kg * (t.txn_type = 'outbound')::INTEGER) "
        f"{_JOIN}"
    ),
    "cast_fn": (
        "SELECT i.category, SUM(i.quantity_kg * CAST(t.txn_type = 'outbound' AS INTEGER)) "
        f"{_JOIN}"
    ),
    "avg_bool": f"SELECT i.category, AVG(i.unit_cost_myr * (t.quantity_kg > 0)::INTEGER) {_JOIN}",
    "sign": f"SELECT i.category, SUM(i.quantity_kg * SIGN(t.quantity_kg)) {_JOIN}",
}


@pytest.mark.parametrize("name", sorted(_FLAG_FANOUT))
def test_a_comparison_or_sign_flag_does_not_hide_a_parent_measure(
    gate_lake: Path, name: str
) -> None:
    from dms_executor.served_gate import served_result_reason

    why = served_result_reason(
        _FLAG_FANOUT[name], warehouse=gate_lake, as_of="2026-10-16T00:00:00Z"
    )
    assert why == "fanout_subject_keys", name


def test_a_child_flag_count_is_kept(gate_lake: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    for sql in (
        f"SELECT i.category, SUM((t.txn_type = 'outbound')::INTEGER) {_JOIN}",
        f"SELECT i.category, COUNT_IF(t.txn_type = 'outbound') {_JOIN}",
    ):
        why = served_result_reason(sql, warehouse=gate_lake, as_of="2026-10-16T00:00:00Z")
        assert why is None, (sql, why)


def test_a_values_list_holding_a_subquery_is_not_an_unnamed_scan(gate_lake: Path) -> None:
    """DuckDB plans these as EXPRESSION_SCAN; the table read sits under it and is checked there."""
    from dms_executor.generative_ask import validate_compiled_sql

    for sql in (
        "SELECT x AS total_kg FROM (VALUES ((SELECT SUM(quantity_kg) FROM inventory))) v(x)",
        "WITH c(label, kg) AS (VALUES ('all', (SELECT SUM(quantity_kg) FROM inventory))) "
        "SELECT label, kg FROM c",
    ):
        assert validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake) is None, sql
    hidden = "SELECT x AS n FROM (VALUES ((SELECT COUNT(*) FROM alerts))) v(x)"
    why = validate_compiled_sql(hidden, grantable={"inventory"}, warehouse=gate_lake)
    assert why == "ungranted:alerts", why


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT * FROM "bronze.sheet1" WHERE false',
        'SELECT COUNT(*) AS n FROM "bronze.sheet1"',
        "SELECT n FROM bronze.sheet1",
        "SELECT COUNT(*) AS n FROM bronze.sheet1",
    ],
)
def test_a_grant_that_names_an_upload_and_a_dotted_lake_table_opens_neither(
    tmp_path: Path, sql: str
) -> None:
    """A grant "bronze.sheet1" cannot say which of the two it means."""
    import duckdb
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.generative_ask import validate_compiled_sql

    db = tmp_path / "ambiguous.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE SCHEMA bronze")
        con.execute("CREATE TABLE bronze.sheet1 AS SELECT 1 AS n")
        con.execute('CREATE TABLE "bronze.sheet1" AS SELECT 2 AS v')
    finally:
        con.close()
    grant = {"sheet1", "bronze.sheet1", "inventory"}
    assert validate_compiled_sql(sql, grantable=grant, warehouse=db) == (
        "ungranted:bronze.sheet1"
    ), sql
    lake = "SELECT sku, quantity_kg FROM inventory"
    assert validate_compiled_sql(lake, grantable=grant, warehouse=db) is None


# --- Round 9: names checked as DuckDB binds them; every BOOLEAN factor is a flag ---


_ODD_NAMES = {
    "dotted": ('"secret.inventory"', {"inventory"}),
    "leading_space": ('" inventory"', {"inventory"}),
    "brackets": ('"[inventory]"', {"inventory"}),
    "trailing_dot": ('"secret."', {"inventory", "secret"}),
    "blank": ('" "', {"inventory"}),
    "kelvin": ('"stocK"', {"stock"}),
    "other_dotted": ('"eur.book"', {"usd.book"}),
    "bare_upload_name": ("sheet1", {"bronze.sheet1"}),
}


@pytest.mark.parametrize("name", sorted(_ODD_NAMES))
@pytest.mark.parametrize("shape", ["COUNT(*) AS n", "MAX(n) AS m"])
def test_a_statistics_answer_checks_the_exact_table_name(
    tmp_path: Path, name: str, shape: str
) -> None:
    """The bare-name check strips quotes, spaces and brackets and keeps the last
    dot part; DuckDB answers these from statistics, so its plan scans nothing."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(
        tmp_path,
        "odd.duckdb",
        'CREATE TABLE "secret.inventory" AS SELECT 1 AS n FROM range(11)',
        'CREATE TABLE " inventory" AS SELECT 1 AS n',
        'CREATE TABLE "[inventory]" AS SELECT 1 AS n',
        'CREATE TABLE "secret." AS SELECT 1 AS n',
        'CREATE TABLE " " AS SELECT 1 AS n',
        'CREATE TABLE "stocK" AS SELECT 1 AS n',
        'CREATE TABLE "eur.book" AS SELECT 2 AS n',
        "CREATE SCHEMA bronze",
        "CREATE TABLE bronze.sheet1 AS SELECT 1 AS n",
        "CREATE TABLE sheet1 AS SELECT 9 AS n",
    )
    cited, grant = _ODD_NAMES[name]
    why = validate_compiled_sql(f"SELECT {shape} FROM {cited}", grantable=grant, warehouse=db)
    assert why is not None and why.startswith("ungranted"), (name, why)


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT COUNT(*) AS n FROM main."bronze.sheet1"',
        'SELECT COUNT(*) AS n FROM "MAIN"."BRONZE.SHEET1"',
        'SELECT * FROM (SELECT MAX(n) AS m FROM main."bronze.sheet1")',
        'SELECT COUNT(*) AS n FROM lake.main."bronze.sheet1"',
    ],
)
def test_a_main_qualified_name_does_not_skip_the_ambiguity_guard(tmp_path: Path, sql: str) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(
        tmp_path,
        "lake.duckdb",
        "CREATE SCHEMA bronze",
        "CREATE TABLE bronze.sheet1 AS SELECT 1 AS n FROM range(3)",
        'CREATE TABLE "bronze.sheet1" AS SELECT 1 AS n FROM range(7)',
    )
    why = validate_compiled_sql(sql, grantable={"bronze.sheet1"}, warehouse=db)
    assert why == "ungranted:bronze.sheet1", (sql, why)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM inventory",
        "SELECT COUNT(*) AS n FROM INVENTORY",
        'SELECT COUNT(*) AS n FROM "Inventory"',
        "SELECT COUNT(*) AS n FROM main.inventory",
        "SELECT COUNT(*) AS n FROM lake.main.inventory",
        "SELECT COUNT(*) AS n FROM lake.inventory",
        'SELECT COUNT(*) AS n FROM "warehouse"."inventory"',
        "SELECT COUNT(*) AS n FROM warehouse_inventory",
        "WITH c AS (SELECT sku FROM inventory) SELECT COUNT(*) AS n FROM c",
        "WITH inventory AS (SELECT sku FROM inventory) SELECT COUNT(*) AS n FROM inventory",
        "SELECT category, SUM(quantity_kg) AS kg FROM inventory GROUP BY category",
    ],
)
def test_a_granted_lake_table_passes_however_it_is_written(tmp_path: Path, sql: str) -> None:
    """lake.inventory names the catalog (the lake file is lake.duckdb); the
    warehouse schema and warehouse_ prefix are Cortex's names for lake tables."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(tmp_path, "lake.duckdb")
    assert validate_compiled_sql(sql, grantable={"inventory"}, warehouse=db) is None, sql


_BOOLEAN_FLAG_FANOUT = {
    "contains": f"SELECT i.category, SUM(i.quantity_kg * contains(t.txn_type, 'out')::INT) {_JOIN}",
    "starts_with": (
        f"SELECT i.category, SUM(i.quantity_kg * starts_with(t.txn_type, 'o')::INT) {_JOIN}"
    ),
    "ends_with": f"SELECT i.category, SUM(i.quantity_kg * ends_with(t.txn_type, 'd')::INT) {_JOIN}",
    "regexp_matches": (
        f"SELECT i.category, SUM(i.quantity_kg * regexp_matches(t.txn_type, 'o')::INT) {_JOIN}"
    ),
    "list_contains": (
        "SELECT i.category, "
        f"SUM(i.quantity_kg * list_contains([t.txn_type], 'outbound')::INT) {_JOIN}"
    ),
    "boolean_cast": (
        f"SELECT i.category, SUM(i.quantity_kg * CAST(t.quantity_kg AS BOOLEAN)::INT) {_JOIN}"
    ),
    "boolean_column": f"SELECT i.category, SUM(i.quantity_kg * t.is_out::INT) {_JOIN}",
    # Round 10: list operators sqlglot does not type, and a BOOLEAN[] overload.
    "overlap_operator": (
        f"SELECT i.category, SUM(i.quantity_kg * ([t.txn_type] && ['outbound'])::INT) {_JOIN}"
    ),
    "contains_operator": (
        f"SELECT i.category, SUM(i.quantity_kg * ([t.txn_type] @> ['outbound'])::INT) {_JOIN}"
    ),
    "contained_operator": (
        f"SELECT i.category, SUM(i.quantity_kg * (['outbound'] <@ [t.txn_type])::INT) {_JOIN}"
    ),
    "list_has_any": (
        "SELECT i.category, "
        f"SUM(i.quantity_kg * list_has_any(['outbound'], [t.txn_type])::INT) {_JOIN}"
    ),
    "json_exists": (
        "SELECT i.category, "
        f"SUM(i.quantity_kg * json_exists(to_json({{'k': t.txn_type}}), '$.k')::INT) {_JOIN}"
    ),
}


@pytest.mark.parametrize("name", sorted(_BOOLEAN_FLAG_FANOUT))
def test_a_boolean_factor_does_not_hide_a_parent_measure(tmp_path: Path, name: str) -> None:
    from dms_executor.served_gate import served_result_reason

    db = _lake_with(
        tmp_path,
        "flags.duckdb",
        "ALTER TABLE transactions ADD COLUMN is_out BOOLEAN",
        "UPDATE transactions SET is_out = (txn_type = 'outbound')",
    )
    why = served_result_reason(
        _BOOLEAN_FLAG_FANOUT[name], warehouse=db, as_of="2026-10-16T00:00:00Z"
    )
    assert why == "fanout_subject_keys", name


def test_a_child_boolean_count_is_kept(tmp_path: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    db = _lake_with(
        tmp_path,
        "flags.duckdb",
        "ALTER TABLE transactions ADD COLUMN is_out BOOLEAN",
        "UPDATE transactions SET is_out = (txn_type = 'outbound')",
    )
    for sql in (
        f"SELECT i.category, SUM(contains(t.txn_type, 'out')::INT) {_JOIN}",
        f"SELECT i.category, COUNT_IF(starts_with(t.txn_type, 'o')) {_JOIN}",
        f"SELECT i.category, SUM(t.is_out::INT) {_JOIN}",
        f"SELECT i.category, SUM(t.quantity_kg * i.unit_cost_myr) {_JOIN}",
        f"SELECT i.category, SUM(([t.txn_type] && ['outbound'])::INT) {_JOIN}",
    ):
        why = served_result_reason(sql, warehouse=db, as_of="2026-10-16T00:00:00Z")
        assert why is None, (sql, why)


@pytest.mark.parametrize("mark", ["\u0085", " ", " ", " "])
def test_a_separator_duckdb_keeps_in_a_name_cannot_hide_a_table(tmp_path: Path, mark: str) -> None:
    """sqlglot splits zz<mark> into the granted zz; DuckDB reads the table zz<mark>."""
    from dms_executor.generative_ask import validate_compiled_sql

    db = _lake_with(
        tmp_path,
        "split.duckdb",
        "CREATE TABLE zz AS SELECT 1 AS n",
        f'CREATE TABLE "zz{mark}" AS SELECT 1 AS n FROM range(9)',
    )
    for sql in (f"SELECT COUNT(*) AS n FROM zz{mark} t", f"SELECT * FROM zz{mark} LIMIT 0"):
        why = validate_compiled_sql(sql, grantable={"zz", "inventory"}, warehouse=db)
        assert why == "hostile_sql:sql_not_analyzable", (repr(sql), why)


_UNPROBED_JOINS = {
    "paren_join": (
        "SELECT SUM(i.quantity_kg) AS kg FROM (inventory i JOIN transactions t ON t.sku = i.sku)"
    ),
    "paren_join_grouped": (
        "SELECT i.category AS category, SUM(i.quantity_kg) AS kg "
        "FROM (inventory i JOIN transactions t ON t.sku = i.sku) GROUP BY i.category"
    ),
    "correlated_join": (
        "SELECT i.sku AS sku, (SELECT SUM(i2.quantity_kg) FROM inventory i2 "
        "JOIN transactions t ON t.sku = i2.sku WHERE i2.sku = i.sku) AS kg FROM inventory i"
    ),
    # Round 10: the same parenthesised join one level down is still unplaceable.
    "paren_join_in_derived": (
        "SELECT SUM(x.q) AS kg FROM (SELECT i.quantity_kg AS q "
        "FROM (inventory i JOIN transactions t ON t.sku = i.sku)) x"
    ),
    "paren_join_in_cte": (
        "WITH x AS (SELECT i.quantity_kg AS q FROM (inventory i JOIN transactions t "
        "ON t.sku = i.sku)) SELECT SUM(q) AS kg FROM x"
    ),
    "paren_join_in_cte_grouped": (
        "WITH x AS (SELECT i.category AS c, i.quantity_kg AS q FROM (inventory i "
        "JOIN transactions t ON t.sku = i.sku)) SELECT c, SUM(q) AS kg FROM x GROUP BY c"
    ),
    "paren_join_in_correlated": (
        "SELECT i.sku AS sku, (SELECT SUM(i2.quantity_kg) FROM (inventory i2 "
        "JOIN transactions t ON t.sku = i2.sku) WHERE i2.sku = i.sku) AS kg FROM inventory i"
    ),
}


@pytest.mark.parametrize("name", sorted(_UNPROBED_JOINS))
def test_a_join_the_probe_cannot_place_is_not_certified(gate_lake: Path, name: str) -> None:
    from dms_executor.served_gate import served_result_reason

    why = served_result_reason(
        _UNPROBED_JOINS[name], warehouse=gate_lake, as_of="2026-10-16T00:00:00Z"
    )
    assert why == "grain_unprobed", (name, why)


def test_a_correlated_count_without_a_join_and_a_derived_table_are_kept(gate_lake: Path) -> None:
    from dms_executor.served_gate import served_result_reason

    for sql in (
        "SELECT l.location_id, (SELECT COUNT(*) FROM alerts a "
        "WHERE a.location_id = l.location_id) AS n FROM locations l",
        "SELECT SUM(x.kg) AS kg FROM (SELECT quantity_kg AS kg FROM inventory) x",
    ):
        why = served_result_reason(sql, warehouse=gate_lake, as_of="2026-10-16T00:00:00Z")
        assert why is None, (sql, why)


#: Words written with ZWJ/ZWNJ (Indic, Persian) and emoji sequences are text.
_LITERAL_MARKS = {
    "zwj": "SELECT sku, quantity_kg FROM inventory WHERE category = 'ka‍na'",
    "zwnj_persian": "SELECT sku, quantity_kg FROM inventory WHERE category = 'می‌خواهم'",
    "emoji_zwj": "SELECT sku, quantity_kg FROM inventory WHERE category = 'a\U0001F468‍\U0001F469'",
    "separator": "SELECT sku, quantity_kg FROM inventory WHERE category = 'a b'",
    "escaped_quote": "SELECT sku, quantity_kg FROM inventory WHERE category = 'it''s‍'",
    "two_literals": (
        "SELECT sku, quantity_kg FROM inventory WHERE category IN ('a‍', 'b‌')"
    ),
}


@pytest.mark.parametrize("name", sorted(_LITERAL_MARKS))
def test_a_format_character_inside_a_literal_is_text(gate_lake: Path, name: str) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    sql = _LITERAL_MARKS[name]
    assert validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake) is None, name


#: Outside a plain literal both parsers agree on, the character is still refused.
_OUTSIDE_MARKS = {
    "after_literal": "SELECT sku FROM inventory WHERE category = 'a'​OR 1 = 1",
    "table_position": "SELECT sku FROM inventory WHERE EXISTS (SELECT 1 FROM​alerts)",
    "quoted_name": 'SELECT sku FROM "inv‍entory"',
    "e_string": "SELECT sku FROM inventory WHERE category = E'a‍'",
    "dollar_string": "SELECT sku FROM inventory WHERE category = $$a‍$$",
    "line_comment": "SELECT sku FROM inventory -- n‍\nWHERE 1 = 1",
    "block_comment": "SELECT sku FROM inventory /* '‍' */",
    "quote_in_comment": (
        "SELECT sku FROM inventory -- it's\n"
        "WHERE category = 'a' OR EXISTS (SELECT 1 FROM​alerts)"
    ),
}


@pytest.mark.parametrize("name", sorted(_OUTSIDE_MARKS))
def test_a_format_character_outside_an_agreed_literal_is_refused(
    gate_lake: Path, name: str
) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    why = validate_compiled_sql(
        _OUTSIDE_MARKS[name], grantable={"inventory"}, warehouse=gate_lake
    )
    assert why == "hostile_sql:sql_not_analyzable", (name, why)


#: In e'\'--' DuckDB reads an escaped quote and sqlglot ends the string, so the
#: -- hides from the syntax tree a tail DuckDB runs. A statistics answer there
#: scans nothing; DuckDB's own parser names the table.
_TOKENIZER_SPLITS = {
    "e_string_count": (
        "SELECT sku FROM inventory WHERE category = e'\\'--' "
        "OR 1 = (SELECT COUNT(*) FROM alerts)"
    ),
    "upper_e_string_max": (
        "SELECT sku FROM inventory WHERE category = E'\\'--' "
        "OR 1 = (SELECT MAX(alert_id) FROM alerts)"
    ),
    "e_string_limit_zero": (
        "SELECT sku FROM inventory WHERE category = e'\\'--' "
        "OR EXISTS (SELECT * FROM alerts LIMIT 0)"
    ),
}


@pytest.mark.parametrize("name", sorted(_TOKENIZER_SPLITS))
def test_a_table_only_duckdb_parses_is_grant_checked(gate_lake: Path, name: str) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    sql = _TOKENIZER_SPLITS[name]
    assert validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake) == (
        "ungranted:alerts"
    ), name
    granted = validate_compiled_sql(sql, grantable={"inventory", "alerts"}, warehouse=gate_lake)
    assert granted is None or not granted.startswith("ungranted"), (name, granted)


#: A table read only DuckDB's parser sees, through a relation other than a name.
_HIDDEN_READERS = {
    "query_table": "SELECT COUNT(*) FROM query_table('alerts')",
    "query": "SELECT COUNT(*) FROM query('SELECT * FROM alerts')",
    "describe": "SELECT COUNT(*) FROM (DESCRIBE alerts)",
}


@pytest.mark.parametrize("name", sorted(_HIDDEN_READERS))
def test_a_reader_only_duckdb_parses_is_refused(gate_lake: Path, name: str) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    sql = (
        "SELECT sku FROM inventory WHERE category = e'\\'--' "
        f"OR 1 = ({_HIDDEN_READERS[name]})"
    )
    why = validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake)
    assert why in {"hostile_sql:file_reader", "ungranted:alerts"}, (name, why)


def test_a_cte_shadows_a_table_only_inside_its_own_with(gate_lake: Path) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    def why(sql: str) -> str | None:
        return validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake)

    # The first COUNT reads the table alerts; the WITH below names its own.
    assert why(
        "SELECT sku FROM inventory WHERE category = e'\\'--' "
        "OR 1 = (SELECT COUNT(*) FROM alerts) "
        "OR 2 = (WITH alerts AS (SELECT 1 AS n) SELECT COUNT(*) FROM alerts)"
    ) == "ungranted:alerts"
    # A recursive CTE's anchor reads the table of that name.
    assert why(
        "SELECT sku FROM inventory WHERE category = e'\\'--' OR 1 = ("
        "WITH RECURSIVE alerts AS (SELECT COUNT(*) AS n FROM alerts "
        "UNION ALL SELECT n FROM alerts WHERE false) SELECT MAX(n) FROM alerts)"
    ) == "ungranted:alerts"
    assert why(
        "WITH a AS (SELECT sku FROM inventory), b AS (SELECT sku FROM a) "
        "SELECT COUNT(*) AS n FROM b WHERE sku <> e'\\'--'"
    ) is None


#: Legit shapes DuckDB's binder cannot bind against placeholder columns
#: (get_table_names). The parse tree binds nothing.
_PARSER_ONLY_SHAPES = (
    "SELECT COUNT(*) AS n FROM inventory JOIN inventory USING (sku)",
    "SELECT sku, COLUMNS('quantity.*') FROM inventory",
    "SELECT i.sku, u.v FROM inventory i, unnest([i.quantity_kg]) u(v)",
    "SELECT w.category, i.sku FROM (VALUES ('RAW', 1.0)) AS w(category, weight) "
    "JOIN inventory i USING (category)",
    "SELECT c.category, COUNT(i.sku) AS n FROM (SELECT DISTINCT category FROM inventory) c "
    "CROSS JOIN (SELECT DISTINCT location_id FROM inventory) l "
    "LEFT JOIN inventory i ON i.category = c.category AND i.location_id = l.location_id "
    "GROUP BY c.category",
)


@pytest.mark.parametrize("sql", _PARSER_ONLY_SHAPES)
def test_the_parser_check_binds_nothing(gate_lake: Path, sql: str) -> None:
    from dms_executor.generative_ask import validate_compiled_sql

    why = validate_compiled_sql(sql, grantable={"inventory"}, warehouse=gate_lake)
    assert why is None or not why.startswith(("hostile_sql", "ungranted")), (sql, why)


# --- Rounds 10 and 11 through POST /v1/chat/ask (rule 10a: the envelope) ---


_ORDERED_SUBQUERY = (
    "SELECT i.category, SUM(i.quantity_kg * (SELECT t.quantity_kg FROM transactions t "
    "WHERE t.sku = i.sku ORDER BY t.quantity_kg DESC LIMIT 1)) AS v "
    "FROM inventory i GROUP BY i.category"
)


def test_an_ordered_subquery_in_an_aggregate_is_answered_not_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """A subquery's ORDER BY clause has no expression; the check crashed into a 503."""
    db = tmp_path / "ordered.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, _ORDERED_SUBQUERY, monkeypatch, minter)
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert _cells(env["rows"]) == _cells(_executed(db, _ORDERED_SUBQUERY))


def test_a_check_that_raises_abstains_instead_of_failing_the_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    import dms_executor.served_gate as gate

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AttributeError("probe broke")

    monkeypatch.setattr(gate, "_fanout", _boom)
    db = tmp_path / "boom.duckdb"
    ensure_demo_warehouse(db)
    env, _ = _prod_post(db, _SKU_PER_LOCATION, monkeypatch, minter)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []


def test_a_table_only_duckdb_parses_abstains_through_the_ask_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Finance does not grant alerts; an e'' string hides it from the syntax tree."""
    db = tmp_path / "estring.duckdb"
    ensure_demo_warehouse(db)
    sql = _TOKENIZER_SPLITS["e_string_count"]
    env, _ = _prod_post(db, sql, monkeypatch, minter, rows_sql="SELECT sku FROM inventory")
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "alerts" not in str(env.get("text") or "")


def test_a_filter_value_with_a_joiner_is_served_through_the_ask_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """A category written with ZWJ is text in a literal both parsers agree on."""
    db = _lake_with(
        tmp_path,
        "joiner.duckdb",
        "INSERT INTO inventory SELECT 'SKU-ZWJ', location_id, 7.0, reorder_level_kg, "
        "unit_cost_myr, supplier_id, 'ka‍na', expiry_date FROM inventory LIMIT 1",
    )
    sql = "SELECT sku, quantity_kg FROM inventory WHERE category = 'ka‍na'"
    env, _ = _prod_post(db, sql, monkeypatch, minter)
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert _cells(env["rows"]) == _cells(_executed(db, sql))
    assert len(env["rows"]) == 1
