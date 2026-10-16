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
