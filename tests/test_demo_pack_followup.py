"""EPIC-020 leftovers: demo pack metrics + DR-0002 follow-ups on POST /v1/chat/ask.

Hard rule 10/10a: assert badge, text, rows, values on the HTTP envelope.
F83: pack hit Cortex-submits SQL and appends the ledger; no local DuckDB fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.demo_pack import (
    SPEND_BY_COUNTRY_Q,
    SPEND_BY_COUNTRY_SQL,
    STOCK_BY_CATEGORY_Q,
    TOTAL_SPEND_Q,
)
from dms_executor.demo_warehouse import execute_sql
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"


@dataclass
class _PackCortex:
    """Pack hit must submit SQL; Ops spend miss may ask."""

    warehouse: Path
    asks: list[AskRequest] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    submit_ok: bool = True
    append_entry_id: str = "led_pack"
    append_hash: str = "hash_pack_not_entry"

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        if not self.submit_ok:
            return QueryResult(ok=False, status="rejected", run_id="run_pack_fail")
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            body = getattr(req, "body", None)
            sql = body.get("sql") if isinstance(body, dict) else getattr(body, "sql", None)
            rows = execute_sql(str(sql), path=self.warehouse)
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_pack_sql",
                output={"rows": rows},
            )
        return QueryResult(ok=True, status="bound", run_id="run_pack_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id=self.append_entry_id, hash=self.append_hash)

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Cannot answer from this Space.",
            badge="abstain",
            abstained=True,
            audit_id="aud_pack_miss",
            route="abstain",
        )


@dataclass
class _LakeSpendAskCortex:
    """Pack submit miss: Cortex still matched cq_spend_by_country with grant soup."""

    asks: list[AskRequest] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(ok=False, status="rejected", run_id="run_miss")
        return QueryResult(ok=True, status="bound", run_id="run_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_x", hash="led_x")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="MY 20,516.00; SG 7,524.00; TH 1,800.00.",
            badge="certified",
            sql_used=SPEND_BY_COUNTRY_SQL,
            rows=[
                {"country": "MY", "total_spend_myr": 20516.00},
                {"country": "SG", "total_spend_myr": 7524.00},
                {"country": "TH", "total_spend_myr": 1800.00},
            ],
            assumptions="cq_spend_by_country",
            audit_id="aud_cq_spend_http",
            route="certified_metric",
            contributing_sources=[
                {
                    "ref_id": "src_inv",
                    "container": "inventory",
                    "member": "category",
                    "kind": "sql",
                    "row_count": 3,
                    "contribution": 0.5,
                },
                {
                    "ref_id": "src_sup",
                    "container": "suppliers",
                    "member": "country",
                    "kind": "sql",
                    "row_count": 3,
                    "contribution": 0.5,
                },
            ],
            drillthrough_token="dt_cq_http",
        )


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "pack.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    return path


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    return m


def _live_client(
    warehouse: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    cortex: Any,
) -> TestClient:
    from dms_api import settings as settings_mod
    from dms_executor.demo_grants import DemoSessionStore

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=warehouse,
        session_store=DemoSessionStore(
            uploads=lambda: (
                "bronze.q3_regional_report_Summary",
                "bronze.q3_regional_report_Detail",
            )
        ),
    )
    app.state.cortex = cortex
    return TestClient(app)


def test_finance_spend_by_country_is_governed_metric(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    cortex = _PackCortex(warehouse=warehouse)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": SPEND_BY_COUNTRY_Q,
            "space_id": FINANCE,
            "session_id": "ses_fin",
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["rows"]
    countries = {str(row.get("country")) for row in env["rows"]}
    assert {"MY", "SG", "TH"} <= countries
    assert "MY" in env["text"]
    assert env["values"]
    assert any(isinstance(v.get("value"), (int, float)) for v in env["values"])
    assert cortex.asks == []
    sql_submits = [
        s
        for s in cortex.submits
        if isinstance(getattr(s, "plan", None), dict) and s.plan.get("kind") == "sql"
    ]
    assert sql_submits, "pack hit must Cortex-submit the metric SQL (F83)"
    body = sql_submits[0].body
    submitted = body.get("sql") if isinstance(body, dict) else getattr(body, "sql", None)
    assert submitted == SPEND_BY_COUNTRY_SQL
    assert cortex.appends
    assert cortex.appends[0].event_type == "ask.governed_metric"
    assert env["audit_id"] == "led_pack"
    assert "scope conflict" not in env["text"].lower()


def test_ops_does_not_answer_spend_by_country(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    cortex = _PackCortex(warehouse=warehouse)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": SPEND_BY_COUNTRY_Q,
            "space_id": WAREHOUSE_OPS,
            "session_id": "ses_ops",
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert len(cortex.asks) == 1


def test_stock_by_category_answers_in_both_spaces(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    cortex = _PackCortex(warehouse=warehouse)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    for space, sid in ((FINANCE, "ses_fin2"), (WAREHOUSE_OPS, "ses_ops2")):
        r = client.post(
            "/v1/chat/ask",
            json={"question": STOCK_BY_CATEGORY_Q, "space_id": space, "session_id": sid},
        )
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        assert env["abstained"] is False, env["text"]
        assert env["badge"] == "L1_GOVERNED_METRIC"
        assert len(env["values"]) > 1
        assert "PACKAGING" in env["text"] or any(
            row.get("category") == "PACKAGING" for row in env["rows"]
        )
        assert "scope conflict" not in env["text"].lower()
    assert cortex.asks == []


def test_followup_average_of_them_after_stock(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    cortex = _PackCortex(warehouse=warehouse)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    parent = client.post(
        "/v1/chat/ask",
        json={
            "question": STOCK_BY_CATEGORY_Q,
            "space_id": FINANCE,
            "session_id": "ses_follow",
        },
    )
    assert parent.status_code == 200, parent.text
    parent_env = parent.json()
    assert parent_env["abstained"] is False
    assert len(parent_env["values"]) > 1
    asks_before = len(cortex.asks)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "average of them",
            "space_id": FINANCE,
            "session_id": "ses_follow",
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert len(env["values"]) == 1
    assert "Average" in env["text"] or "average" in env["text"]
    assert len(cortex.asks) == asks_before


def test_scalar_total_spend_then_add_2000(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    cortex = _PackCortex(warehouse=warehouse)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    parent = client.post(
        "/v1/chat/ask",
        json={
            "question": TOTAL_SPEND_Q,
            "space_id": FINANCE,
            "session_id": "ses_scalar",
        },
    )
    assert parent.status_code == 200, parent.text
    parent_env = parent.json()
    assert parent_env["abstained"] is False
    assert len(parent_env["values"]) == 1
    prior = float(parent_env["values"][0]["value"])
    r = client.post(
        "/v1/chat/ask",
        json={"question": "add 2000", "space_id": FINANCE, "session_id": "ses_scalar"},
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert len(env["values"]) == 1
    assert abs(float(env["values"][0]["value"]) - (prior + 2000.0)) < 0.011
    assert "2000" in env["text"] or "2,000" in env["text"]


def test_followup_without_prior_abstains() -> None:
    from dms_executor.session_followup import maybe_followup

    env = maybe_followup("average of them", prior=None, session_id="ses_none")
    assert env is not None
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert_envelope_valid(env)


def test_cortex_fallback_spend_does_not_f32_demote(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pack submit miss + cq_spend_by_country rows + bronze grant soup must stay certified."""
    cortex = _LakeSpendAskCortex()
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": SPEND_BY_COUNTRY_Q,
            "space_id": FINANCE,
            "session_id": "ses_cq_fallback",
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"]
    assert "20,516.00" in env["text"] or "20516" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert len(cortex.asks) == 1
