"""VQ-01 — certified categoty synonym on POST /v1/chat/ask.

Hard rule 10/10a: assert text, rows, badge, values, audit_id on the HTTP path.
Cortex pack match is Cortex#125; this file is the DMS envelope half.

Asset-declared scope is warehouse ``transactions`` JOIN ``inventory`` (not the
Excel Sales sheet). Wide_Fill-class totals must not ship under L0. Sibling
E9-02 still demotes when executed SQL cites Wide_Fill without a unique pin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

# SCORE-03 / E9-02 wrong-sheet class. Must not appear under L0_CERTIFIED.
_WIDE_FILL_CLASS = (383803.56, 242755.97, 228548.84)

# Asset-declared warehouse ranks (not Excel Sales / Wide_Fill).
_WAREHOUSE_RANKS = (
    ("CHEMICALS", 125000.50),
    ("FOOD_COLD", 98000.25),
    ("PARTS", 76100.00),
)

_CERTIFIED_SQL = (
    "SELECT i.category, "
    "ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions t JOIN inventory i ON t.sku = i.sku "
    "WHERE t.txn_type = 'OUT' GROUP BY i.category "
    "ORDER BY sales_value_myr DESC, i.category ASC LIMIT 3"
)


@dataclass
class _CertifiedCategotyCortex:
    """Engine L0 hit: curated categoty synonym, warehouse SQL, executed ranks."""

    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-vq01")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        ranks = _WAREHOUSE_RANKS
        parts = [f"{cat} {val:,.2f}" for cat, val in ranks]
        return AskResponse(
            answer="Top 3: " + ", ".join(parts) + ".",
            audit_id="aud_vq01_http",
            route="sql",
            badge="certified",
            sql_used=_CERTIFIED_SQL,
            rows=[
                {"category": cat, "sales_value_myr": val} for cat, val in ranks
            ],
            drillthrough_token="dt_vq01",
        )


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
            issued_at="2026-08-05T00:00:00+00:00",
            expires_at="2026-08-05T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    return m


def test_chat_ask_post_categoty_certified_warehouse_ranks(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask — categoty synonym stays L0 with executed warehouse ranks."""
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = _CertifiedCategotyCortex()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
    )
    app.state.cortex = cortex
    client = TestClient(app)

    body = client.post(
        "/v1/chat/ask",
        json={
            "question": "show top 3 categoty sales",
            "session_id": "ses_vq01",
        },
    ).json()

    assert_envelope_valid(body)
    assert body["abstained"] is False
    assert body["badge"] == "L0_CERTIFIED"
    assert len(body["rows"]) == 3
    assert body["text"]
    prev = None
    for cat, val in _WAREHOUSE_RANKS:
        assert cat in body["text"]
        assert f"{val:,.2f}" in body["text"]
        row = next(r for r in body["rows"] if r["category"] == cat)
        assert float(row["sales_value_myr"]) == val
        assert prev is None or val <= prev
        prev = val
    sql = (body.get("sql_used") or "").lower()
    assert "wide_fill" not in sql
    assert "transactions" in sql
    assert "inventory" in sql
    for n in ("383,803.56", "242,755.97", "228,548.84"):
        assert n not in body["text"]
    assert body["audit_id"] == "aud_vq01_http"
    assert len(cortex.asks) == 1
    assert cortex.asks[0].question == "show top 3 categoty sales"

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()
