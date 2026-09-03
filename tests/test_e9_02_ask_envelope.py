"""E9-02 — ambiguous ranking demotes on POST /v1/chat/ask without a plant.

Hard rule 10/10a: assert text, rows, badge, values, audit_id on the HTTP path.
The client does not send grounded_tables (the ungrounded demo ACL is DEMO_TABLES).
Wide_Fill in executed SQL must still demote.
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
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

_WIDE_FILL_CLASS = (383803.56, 242755.97, 228548.84)
_F32_COMPETING = [
    "bronze.aa64458a_p50_03_inventory_messy_Sales",
    "bronze.aa64458a_p50_03_inventory_messy_Wide_Fill",
]


@dataclass
class _WideFillRankingCortex:
    """Engine returns the SCORE-03 wrong-sheet ranking under a green badge."""

    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-e902")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        home, sports, misc = _WIDE_FILL_CLASS
        return AskResponse(
            answer=(
                f"Top 3: Home {home:,.2f}, Sports {sports:,.2f}, Misc {misc:,.2f}."
            ),
            audit_id="aud_e902_http",
            route="query_skill",
            provenance={"badge": "query_skill", "layer": "L2"},
            sql_used=(
                "SELECT category, SUM(sales_value_myr) "
                "FROM bronze.aa64458a_p50_03_inventory_messy_Wide_Fill "
                "GROUP BY category ORDER BY 2 DESC LIMIT 3"
            ),
            rows=[
                {"category": "Home", "sales_value_myr": home},
                {"category": "Sports", "sales_value_myr": sports},
                {"category": "Misc", "sales_value_myr": misc},
            ],
            drillthrough_token="dt_e902",
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


def test_chat_ask_post_ungrounded_demotes_wide_fill_ranking(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask with no grounded_tables — E9-02 must still fire."""
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = _WideFillRankingCortex()
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
            "session_id": "ses_e902",
        },
    ).json()

    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["values"] == []
    assert body["rows"] == []
    assert "scope conflict" in body["text"].lower()
    for n in ("383,803.56", "242,755.97", "228,548.84"):
        assert n not in body["text"]
    assert body["audit_id"] == "aud_e902_http"
    assert body["drillthrough_token"] in (None, "")
    assert len(cortex.asks) == 1

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()


@dataclass
class _GroupedTotalCortex:
    """Engine answers a one-number ask with a category ranking (E12 live miss)."""

    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-e12")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Found 10 row(s).",
            audit_id="aud_e12_http",
            route="query_skill",
            provenance={"badge": "query_skill", "layer": "L2"},
            sql_used=(
                "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
                "FROM inventory GROUP BY category LIMIT 1000"
            ),
            rows=[
                {"category": "FOOD_COLD", "total_value_myr": 67710506.66},
                {"category": "CHEMICALS", "total_value_myr": 61894503.52},
            ],
            drillthrough_token="dt_e12",
        )


def test_chat_ask_post_scalar_ask_does_not_ship_ranking(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask — E12: total/quantity ask must not keep a ranking."""
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = _GroupedTotalCortex()
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
            "question": "What is total inventory quantity?",
            "session_id": "ses_e12",
        },
    ).json()

    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["values"] == []
    assert body["rows"] == []
    assert "different question" in body["text"].lower()
    assert "67710506.66" not in body["text"]
    assert body["audit_id"] == "aud_e12_http"
    assert len(cortex.asks) == 1

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()


def test_chat_ask_post_demotes_ambiguous_multi_sheet_ranking(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask when the client DOES name both sheets of one workbook.

    Recovered from the closed #98. The twin above sends no ``grounded_tables``;
    this one sends both sheets, so the conflict has to be derived from the label
    set itself. ``Executor.live_ask`` never plants ``competing_scopes`` - it
    passes ``sorted(acl.row_predicates)`` - so a plant-only demote would green
    this and still ship Wide_Fill totals to a customer.
    """
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = _WideFillRankingCortex()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
        session_store=DemoSessionStore(uploads=lambda: tuple(_F32_COMPETING)),
    )
    app.state.cortex = cortex
    client = TestClient(app)

    body = client.post(
        "/v1/chat/ask",
        json={
            "question": "show top 3 categoty sales",
            "session_id": "ses_e902",
            "grounded_tables": list(_F32_COMPETING),
        },
    ).json()

    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["values"] == []
    assert body["rows"] == []
    assert "scope conflict" in body["text"].lower()
    for n in ("383,803.56", "242,755.97", "228,548.84"):
        assert n not in body["text"]
    assert body["audit_id"] == "aud_e902_http"
    assert body["drillthrough_token"] in (None, "")
    assert len(cortex.asks) == 1

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()
