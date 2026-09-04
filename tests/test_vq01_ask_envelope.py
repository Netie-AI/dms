"""VQ-01 — certified categoty synonym on POST /v1/chat/ask.

Hard rule 10/10a: assert badge, text, rows, values, audit_id on the HTTP path.
Synonyms live on the Cortex pack asset (Cortex#125 ``cq_top3_category_sales``),
never as product intent regex.

Match contract (Cortex pack YAML, not DMS):
- exact: normalize(question) == normalize(asset.question)
- synonym: normalize(question) in normalize(asset.synonyms)
- collision across assets raises on Cortex index build
- DMS maps engine ``certified`` -> ``L0_CERTIFIED``, ``governed_metric`` ->
  ``L1_GOVERNED_METRIC``. DMS does not upgrade L2/L3 to L0.

Asset-declared scope is warehouse ``transactions`` JOIN distinct ``inventory``
sku/category (not the Excel Sales sheet). Category totals must conserve
outbound revenue: ELECTRONICS 8,953,922.60; CHEMICALS 8,799,446.70;
FOOD_COLD 8,754,427.11. The naive ``JOIN inventory`` (lots, not SKUs) inflates
~14.8x and ranks FOOD_DRY second — that must not ship under L0/L1.

Cortex#125 YAML still uses the fan-out JOIN. This file pins the corrected
magnitudes on the DMS envelope. Live HTTP against a reachable stack is skipped
when DMS is down (GitHub CI has no Cortex engine). Sibling E9-02 still demotes
when executed SQL cites Wide_Fill without a unique pin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor, map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

_L0_L1 = frozenset({"L0_CERTIFIED", "L1_GOVERNED_METRIC"})
_VQ01_PHRASES = (
    "show top 3 categoty sales",
    "show top 3 category sales",
    "top 3 category sales",
)

# Corrected warehouse ranks (dms#39). Must conserve outbound revenue 80,375,993.99.
_ORACLE_RANKS = (
    ("ELECTRONICS", 8_953_922.60),
    ("CHEMICALS", 8_799_446.70),
    ("FOOD_COLD", 8_754_427.11),
)
_ORACLE_TOL = 0.02

# Naive JOIN inventory (lot grain) — wrong rank AND ~14.8x magnitudes.
_INFLATED_FANOUT = (
    ("ELECTRONICS", 133_931_869.04),
    ("FOOD_DRY", 130_689_827.09),
    ("CHEMICALS", 130_523_362.43),
)

# SCORE-03 / E9-02 wrong-sheet class. Must not appear under L0/L1.
_WIDE_FILL_CLASS = (
    ("Home", 383_803.56),
    ("Sports", 242_755.97),
    ("Misc", 228_548.84),
)

# Distinct sku,category — the grain that conserves outbound revenue.
_CERTIFIED_SQL = (
    "SELECT c.category, "
    "ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions t "
    "JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku "
    "WHERE t.txn_type = 'OUT' "
    "GROUP BY c.category "
    "ORDER BY sales_value_myr DESC, c.category ASC LIMIT 3"
)

_FANOUT_SQL = (
    "SELECT i.category, "
    "ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions t JOIN inventory i ON t.sku = i.sku "
    "WHERE t.txn_type = 'OUT' GROUP BY i.category "
    "ORDER BY sales_value_myr DESC, i.category ASC LIMIT 3"
)


def _money(n: float) -> str:
    return f"{n:,.2f}"


def _row_measure(row: dict[str, Any]) -> float | None:
    for key in ("sales_value_myr", "value", "total"):
        val = row.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def assert_vq01_category_sales_envelope(env: dict[str, Any]) -> None:
    """Fail if the ask fell to L2/L3, invented totals, or shipped Wide_Fill class."""
    assert_envelope_valid(env)
    badge = env.get("badge")
    assert env.get("abstained") is False, f"VQ-01 abstained under {badge}"
    assert badge in _L0_L1, f"VQ-01 fell through to {badge}"
    rows = list(env.get("rows") or [])
    assert len(rows) == 3, f"expected 3 ranked rows, got {len(rows)}"
    text = str(env.get("text") or "")
    assert text.strip()

    got: list[tuple[str, float]] = []
    for row in rows:
        cat = str(row.get("category") or "")
        val = _row_measure(row)
        assert cat and val is not None, f"row missing category/measure: {row}"
        got.append((cat, val))

    want_cats = [c for c, _ in _ORACLE_RANKS]
    got_cats = [c for c, _ in got]
    assert got_cats == want_cats, f"rank {got_cats} != {want_cats}"
    for (cat, got_v), (_, want_v) in zip(got, _ORACLE_RANKS, strict=True):
        assert abs(got_v - want_v) <= _ORACLE_TOL, (
            f"{cat} {got_v} != oracle {want_v} (fan-out JOIN or wrong sheet)"
        )
        assert cat in text
        assert _money(want_v) in text

    value_nums = [
        float(v["value"])
        for v in (env.get("values") or [])
        if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
    ]
    for _, want_v in _ORACLE_RANKS:
        assert any(abs(n - want_v) <= _ORACLE_TOL for n in value_nums), (
            f"oracle {want_v} missing from values[]"
        )

    forbidden = [v for _, v in _INFLATED_FANOUT] + [v for _, v in _WIDE_FILL_CLASS]
    for n in forbidden:
        assert _money(n) not in text
        assert not any(abs(x - n) <= _ORACLE_TOL for x in value_nums)

    sql = str(env.get("sql_used") or "").lower()
    assert "wide_fill" not in sql
    assert "transactions" in sql
    assert "inventory" in sql


def _certified_response(
    ranks: tuple[tuple[str, float], ...],
    *,
    sql: str,
    badge: str = "certified",
    route: str = "sql",
    audit_id: str = "aud_vq01_http",
) -> AskResponse:
    parts = [f"{cat} {_money(val)}" for cat, val in ranks]
    return AskResponse(
        answer="Top 3: " + ", ".join(parts) + ".",
        audit_id=audit_id,
        route=route,
        badge=badge,
        sql_used=sql,
        rows=[{"category": cat, "sales_value_myr": val} for cat, val in ranks],
        drillthrough_token="dt_vq01",
    )


@dataclass
class _StubCortex:
    """Engine stub: bind ok, ask returns a planted AskResponse."""

    response: AskResponse
    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-vq01")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return self.response


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


def _live_client(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    cortex: _StubCortex,
) -> TestClient:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    return TestClient(app)


def _cleanup_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()


@pytest.mark.parametrize("question", _VQ01_PHRASES)
def test_chat_ask_post_categoty_certified_oracle_ranks(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    """POST /v1/chat/ask — synonym phrases stay L0 with corrected warehouse ranks."""
    cortex = _StubCortex(_certified_response(_ORACLE_RANKS, sql=_CERTIFIED_SQL))
    client = _live_client(minter, monkeypatch, cortex)
    body = client.post(
        "/v1/chat/ask",
        json={"question": question, "session_id": "ses_vq01"},
    ).json()

    assert_vq01_category_sales_envelope(body)
    assert body["badge"] == "L0_CERTIFIED"
    assert body["audit_id"] == "aud_vq01_http"
    assert len(cortex.asks) == 1
    assert cortex.asks[0].question == question
    sql = (body.get("sql_used") or "").lower()
    assert "distinct" in sql
    _cleanup_live_env(monkeypatch)


def test_dms_does_not_upgrade_l2_categoty_to_l0(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fall-through to query_skill must not become L0/L1 on the customer envelope."""
    cortex = _StubCortex(
        _certified_response(
            _ORACLE_RANKS,
            sql=_CERTIFIED_SQL,
            badge="query_skill",
            route="query_skill",
            audit_id="aud_vq01_l2",
        )
    )
    client = _live_client(minter, monkeypatch, cortex)
    body = client.post(
        "/v1/chat/ask",
        json={"question": "show top 3 categoty sales", "session_id": "ses_vq01_l2"},
    ).json()

    assert_envelope_valid(body)
    assert body["badge"] not in _L0_L1
    assert body["badge"] == "L2_VALIDATED"
    _cleanup_live_env(monkeypatch)


def test_vq01_oracle_rejects_l2_fallthrough() -> None:
    env = map_ask_response_to_envelope(
        _certified_response(
            _ORACLE_RANKS,
            sql=_CERTIFIED_SQL,
            badge="query_skill",
            route="query_skill",
        ),
        session_id="ses_vq01_map_l2",
        question="show top 3 categoty sales",
    )
    with pytest.raises(AssertionError, match="fell through"):
        assert_vq01_category_sales_envelope(env)


def test_vq01_oracle_rejects_inflated_fanout() -> None:
    """Fan-out JOIN totals (133M / FOOD_DRY) must not pass the VQ-01 oracle."""
    env = map_ask_response_to_envelope(
        _certified_response(_INFLATED_FANOUT, sql=_FANOUT_SQL),
        session_id="ses_vq01_fanout",
        question="show top 3 categoty sales",
    )
    # Product will map certified -> L0 if Cortex certifies the naive JOIN.
    # The VQ-01 envelope contract must still go red on those magnitudes.
    assert env["badge"] == "L0_CERTIFIED"
    with pytest.raises(AssertionError):
        assert_vq01_category_sales_envelope(env)


def test_vq01_oracle_rejects_wide_fill_class_under_l0() -> None:
    env = map_ask_response_to_envelope(
        _certified_response(
            _WIDE_FILL_CLASS,
            sql=(
                "SELECT category, SUM(sales_value_myr) AS sales_value_myr "
                "FROM bronze.aa64458a_p50_03_inventory_messy_Sales "
                "GROUP BY category ORDER BY 2 DESC LIMIT 3"
            ),
        ),
        session_id="ses_vq01_wf",
        question="show top 3 categoty sales",
        grounded_tables=["bronze.aa64458a_p50_03_inventory_messy_Sales"],
    )
    with pytest.raises(AssertionError):
        assert_vq01_category_sales_envelope(env)


def test_map_certified_oracle_ranks_stay_l0() -> None:
    env = map_ask_response_to_envelope(
        _certified_response(_ORACLE_RANKS, sql=_CERTIFIED_SQL),
        session_id="ses_vq01_map",
        question="show top 3 categoty sales",
    )
    assert_vq01_category_sales_envelope(env)
    assert env["badge"] == "L0_CERTIFIED"


def test_map_governed_metric_oracle_ranks_count_as_l1() -> None:
    env = map_ask_response_to_envelope(
        _certified_response(
            _ORACLE_RANKS,
            sql=_CERTIFIED_SQL,
            badge="governed_metric",
            route="governed_metric",
        ),
        session_id="ses_vq01_l1",
        question="show top 3 categoty sales",
    )
    assert_vq01_category_sales_envelope(env)
    assert env["badge"] == "L1_GOVERNED_METRIC"


def _dms_live_reachable() -> bool:
    if os.environ.get("DMS_VQ01_LIVE") == "0":
        return False
    url = os.environ.get("DMS_URL", "http://127.0.0.1:8090").rstrip("/")
    try:
        import httpx

        r = httpx.get(f"{url}/health", timeout=1.5)
        if r.status_code == 200:
            return True
    except Exception:  # noqa: BLE001 — stack down is skip, not fail
        pass
    return os.environ.get("DMS_VQ01_LIVE") == "1"


@pytest.mark.skipif(
    not _dms_live_reachable(),
    reason=(
        "DMS HTTP not reachable (CI has no Cortex engine). "
        "Set DMS_VQ01_LIVE=1 against a live stack. Pack/match: Cortex#125; "
        "corrected DISTINCT sku,category SQL may still be pending on Cortex."
    ),
)
def test_live_chat_ask_categoty_hits_oracle_envelope() -> None:
    """Live POST /v1/chat/ask — skip unless the stack is up; pin oracle ranks."""
    import httpx

    url = os.environ.get("DMS_URL", "http://127.0.0.1:8090").rstrip("/")
    r = httpx.post(
        f"{url}/v1/chat/ask",
        json={"question": "show top 3 categoty sales", "session_id": "ses_vq01_live"},
        timeout=120.0,
    )
    r.raise_for_status()
    assert_vq01_category_sales_envelope(r.json())
