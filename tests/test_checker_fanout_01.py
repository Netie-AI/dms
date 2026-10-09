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
from dms_executor.served_gate import served_result_reason

_PACK = Path(__file__).resolve().parent / "fixtures" / "curated_ceo" / "questions.yaml"
_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "ask_guide" / "flag_off_52_f9ffc3e1.json"
_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_KEEP = ("cq_chemicals_list", "ops_chemicals_list", "cq_audit_overdue")
_FANOUT_SQL = (
    "SELECT f.sku AS sku, COUNT(*) AS n "
    "FROM inventory f JOIN locations l ON TRUE GROUP BY f.sku"
)


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
            grantable=set(DEMO_TABLES),
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
    env = _ask(db, "stock by location", _FANOUT_SQL, calls)
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
    assert_envelope_valid(env)


def test_constant_select_touching_no_granted_table_is_refused(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    question = _questions()["trap_alerts_ungranted"]
    calls: list[dict[str, Any]] = []

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
            calls.append({"question": question, **kwargs})
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
    blob = f"{env.get('text') or ''} {' '.join(str(a) for a in (env.get('assumptions') or []))}"
    assert "no_granted_table" in blob
    reasons = [
        str((call.get("sql_feedback") or {}).get("reason") or "")
        for call in calls
        if isinstance(call.get("sql_feedback"), dict)
    ]
    assert reasons
    assert all(reason == "no_granted_table" for reason in reasons)
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
            grantable=set(DEMO_TABLES),
        )
        assert why is None, row["id"]
        checked += 1
    assert checked >= 17
