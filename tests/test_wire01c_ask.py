"""WIRE-01c: the contract ask carries dms_payload; Cortex plan+SQL stamps reach the envelope.

Every case goes through POST /v1/chat/ask with the production Executor shape
and asserts the DMS envelope (assert_envelope_valid), its rendered text and
rows, and the request the fake Cortex recorded. Synthetic demo lake only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.answer import AskPayload
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_SERVED_SQL = (
    "SELECT l.location_id AS location_id, COUNT(i.sku) AS sku_count "
    "FROM locations l JOIN inventory i ON i.location_id = l.location_id "
    "GROUP BY l.location_id"
)
_CLOSEST = "How many SKUs does each location hold?"


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
    return minted


def _executed(db: Path, sql: str) -> list[dict[str, Any]]:
    con = connect_file(db)
    try:
        cur = con.execute(sql)
        cols = [str(d[0]) for d in (cur.description or [])]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


class _Cortex:
    """Insights finds nothing, so the ask reaches the contract ask."""

    def __init__(self, db: Path, answer: dict[str, Any]) -> None:
        self.db = db
        self.answer = answer
        self.asks: list[AskRequest] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        _ = (question, kwargs)
        return None

    def submit(self, req: Any) -> QueryResult:
        body = getattr(req, "body", None) or {}
        sql = str(body.get("sql") or "").strip()
        rows = _executed(self.db, sql) if sql else []
        return QueryResult(ok=True, status="ok", run_id="run-w1c", output={"rows": rows})

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(**self.answer)

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="ent-w1c", hash="hash-w1c")


def _post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minted: ManifestMinter,
    answer: dict[str, Any],
    *,
    schema_context: bool,
) -> tuple[dict[str, Any], _Cortex, Executor]:
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    db = tmp_path / "w1c.duckdb"
    ensure_demo_warehouse(db)
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    if schema_context:
        monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    else:
        monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    settings_mod.get_settings.cache_clear()
    cortex = _Cortex(db, answer)
    app = create_app()
    exe = Executor(cortex=cortex, minter=minted)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    res = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": "skus per location", "space_id": _FINANCE, "session_id": "ses_w1c"},
    )
    assert res.status_code == 200, res.text
    return res.json(), cortex, exe


def _served(db_rows: list[dict[str, Any]], **stamps: Any) -> dict[str, Any]:
    return {
        "answer": "Each location holds the SKUs listed.",
        "badge": "session",
        "route": "plan_sql",
        "sql_used": _SERVED_SQL,
        "rows": db_rows,
        "assumptions": ["rung: plan"],
        "audit_id": "aud-w1c",
        **stamps,
    }


def _rows(tmp_path: Path) -> list[dict[str, Any]]:
    db = tmp_path / "rows.duckdb"
    ensure_demo_warehouse(db)
    return _executed(db, _SERVED_SQL)


def test_the_ask_carries_a_payload_of_granted_tables_when_schema_context_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    answer = _served(_rows(tmp_path), plan_sql_rung="plan", served_model="m-1")
    env, cortex, exe = _post(tmp_path, monkeypatch, minter, answer, schema_context=True)
    assert_envelope_valid(env)
    assert cortex.asks, "the contract ask was not reached"
    payload = cortex.asks[0].dms_payload
    assert isinstance(payload, dict), payload
    AskPayload.model_validate(payload)
    granted = {str(t).lower() for t in exe.grantable_tables(space_id=_FINANCE)}
    names = {t["name"].lower() for t in payload["tables"]}
    assert names and names <= granted, (names, granted)


def test_no_payload_when_schema_context_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    answer = _served(_rows(tmp_path))
    env, cortex, _ = _post(tmp_path, monkeypatch, minter, answer, schema_context=False)
    assert_envelope_valid(env)
    assert cortex.asks
    assert cortex.asks[0].dms_payload is None


@pytest.mark.parametrize("rung", ["plan", "error-fed-retry-2", "stronger-model"])
def test_a_plan_sql_answer_keeps_its_rows_and_carries_the_cortex_stamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, rung: str
) -> None:
    rows = _rows(tmp_path)
    answer = _served(
        rows,
        plan_sql_rung=rung,
        served_model="m-1",
        served_provider="openvault",
        model_calls={"plan": 1, rung: 1, "total": 2},
    )
    env, _, _ = _post(tmp_path, monkeypatch, minter, answer, schema_context=True)
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["badge"] == "L2_VALIDATED", env.get("assumptions")
    assert str(env.get("text") or "").strip()
    assert sorted(map(str, env["rows"])) == sorted(map(str, rows))
    assert env["ladder_rung"] == rung
    assert env["plan_origin"] == "generate_sql"
    assert env["served_model"] == "m-1"
    assert env["served_provider"] == "openvault"
    assert env["model_calls_by_step"] == {"plan": 1, rung: 1, "total": 2}


def test_a_reconfirm_is_a_refusal_that_offers_the_closest_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    answer = {
        "answer": "Reconfirm: alerts is outside the grant. Closest question: x. Yes runs it.",
        "badge": "reconfirm",
        "route": "reconfirm",
        "sql_used": None,
        "rows": [],
        "assumptions": ["alerts is outside the grant", "rung: reconfirm"],
        "reconfirm": {"why": "alerts is outside the grant", "closest_question": _CLOSEST},
        "plan_sql_rung": "reconfirm",
        "served_model": "m-1",
        "audit_id": "aud-w1c",
    }
    env, _, _ = _post(tmp_path, monkeypatch, minter, answer, schema_context=True)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert "closest question" in text.lower(), text
    assert "older than the engine" not in text, text
    assert "alerts" not in text, text
    assert "Yes runs" not in text, text
    assert env["suggestions"][0] == _CLOSEST
    assert env["ladder_rung"] == "reconfirm"
    assert "plan_origin" not in env or env["plan_origin"] != "generate_sql"


def test_an_ungranted_plan_sql_answer_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The payload narrows what Cortex may read; DMS still grant-checks what it served."""
    db = tmp_path / "alerts_rows.duckdb"
    ensure_demo_warehouse(db)
    sql = "SELECT alert_id, message FROM alerts"
    answer = _served(_executed(db, sql), sql_used=sql, plan_sql_rung="plan", served_model="m-1")
    env, _, exe = _post(tmp_path, monkeypatch, minter, answer, schema_context=True)
    granted = {str(t).lower() for t in exe.grantable_tables(space_id=_FINANCE)}
    if "alerts" in granted:
        pytest.fail("the finance Space grants alerts; pick another ungranted table")
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
