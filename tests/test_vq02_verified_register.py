"""VQ-02 — Studio register of a Space-scoped verified question→SQL asset.

Hard rule 8/10/10a: mutation calls compliance_gate; ask asserts badge, text,
rows, sql_used on POST /v1/chat/ask. Pack match stays VQ-01 / Cortex#125.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import (
    AskRequest,
    AskResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
)
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.verified_queries import maybe_verified_ask, register_verified_query
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
QUESTION = "VQ-02 steward: capacity of warehouse A?"
SQL = "SELECT name, capacity_kg FROM locations WHERE location_id = 'WH-A'"
_CORTEX_SQL = "SELECT 1 AS cortex_marker"


@dataclass
class _MarkerCortex:
    """Hit path must submit SQL and append the ledger; miss path may ask."""

    asks: list[AskRequest] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    sql_output: dict[str, Any] | None = field(
        default_factory=lambda: {"rows": [{"name": "Warehouse A", "capacity_kg": 100000.0}]}
    )
    append_entry_id: str = "led_vq02"
    append_hash: str = "hash_vq02_not_entry"

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_vq02_sql",
                output=self.sql_output,
            )
        return QueryResult(ok=True, status="bound", run_id="run_vq02")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id=self.append_entry_id, hash=self.append_hash)

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Cortex marker 1.",
            badge="certified",
            sql_used=_CORTEX_SQL,
            rows=[{"cortex_marker": 1}],
            assumptions="vq02-isolation",
            audit_id="aud_vq02_cortex",
            route="sql",
        )


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        seen["action"] = action
        seen["actor"] = actor
        seen.setdefault("actions", []).append(action)
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", allow)
    return seen


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "vq02.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
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
    cortex: _MarkerCortex,
) -> TestClient:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    app = create_app()
    app.state.ask_service = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=warehouse,
    )
    app.state.cortex = cortex
    return TestClient(app)


def test_register_calls_gate_with_configured_actor(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": FINANCE, "question": QUESTION, "sql": SQL},
    )
    assert r.status_code == 200, r.text
    assert seen["action"] == "studio.verified_query"
    assert seen["actor"] == get_settings().dms_actor_user_id
    body = r.json()
    assert body["space_id"] == FINANCE
    assert body["sql"] == SQL
    assert body["asset_id"].startswith("vq_")


def test_denied_gate_does_not_persist(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=False, reason="gate_denied", action=action)

    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", deny)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": FINANCE, "question": QUESTION, "sql": SQL},
    )
    assert r.status_code == 403
    _gate_allows(monkeypatch)
    listed = client.get(f"/v1/studio/verified-queries?space_id={FINANCE}").json()
    assert listed == []


def test_list_does_not_leak_across_spaces(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    assert (
        client.post(
            "/v1/studio/verified-queries",
            json={"space_id": FINANCE, "question": QUESTION, "sql": SQL},
        ).status_code
        == 200
    )
    fin = client.get(f"/v1/studio/verified-queries?space_id={FINANCE}").json()
    ops = client.get(f"/v1/studio/verified-queries?space_id={WAREHOUSE_OPS}").json()
    assert len(fin) == 1
    assert fin[0]["question"] == QUESTION
    assert ops == []


def test_hostile_sql_is_refused(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/verified-queries",
        json={
            "space_id": FINANCE,
            "question": "drop it",
            "sql": "INSERT INTO locations VALUES ('x','x',1,1)",
        },
    )
    assert r.status_code == 400
    assert "hostile_sql" in r.json()["detail"]


def test_ops_cannot_register_finance_table(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/verified-queries",
        json={
            "space_id": WAREHOUSE_OPS,
            "question": QUESTION,
            "sql": "SELECT sku FROM transactions LIMIT 1",
        },
    )
    assert r.status_code == 400
    assert "sql_not_in_space" in r.json()["detail"]
    assert "transactions" in r.json()["detail"]


def test_ask_in_space_is_l0_foreign_space_misses(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex()
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    reg = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": FINANCE, "question": QUESTION, "sql": SQL},
    )
    assert reg.status_code == 200, reg.text

    hit = client.post(
        "/v1/chat/ask",
        json={"question": QUESTION, "space_id": FINANCE, "session_id": "ses_vq02_fin"},
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    assert env["sql_used"] == SQL
    assert env["rows"]
    assert env["rows"][0]["name"] == "Warehouse A"
    assert float(env["rows"][0]["capacity_kg"]) == 100000.0
    assert "Warehouse A" in env["text"]
    assert "100000" in env["text"] or "100,000" in env["text"] or "100000.0" in env["text"]
    assert env["values"]
    sql_submits = [
        s
        for s in cortex.submits
        if isinstance(getattr(s, "plan", None), dict) and s.plan.get("kind") == "sql"
    ]
    assert sql_submits, "VQ hit must Cortex-submit the registered SQL (F83)"
    body = sql_submits[0].body
    submitted_sql = body.get("sql") if isinstance(body, dict) else getattr(body, "sql", None)
    assert submitted_sql == SQL
    assert cortex.asks == []
    assert env["audit_id"] == "led_vq02"
    assert cortex.appends
    assert cortex.appends[0].event_type == "ask.verified_query"
    assert any("Cortex submit" in a for a in (env.get("assumptions") or []))

    miss = client.post(
        "/v1/chat/ask",
        json={
            "question": QUESTION,
            "space_id": WAREHOUSE_OPS,
            "session_id": "ses_vq02_ops",
        },
    )
    assert miss.status_code == 200, miss.text
    foreign = miss.json()
    assert_envelope_valid(foreign)
    assert foreign.get("sql_used") != SQL
    assert not any(
        str(r.get("name")) == "Warehouse A" and float(r.get("capacity_kg") or 0) == 100000.0
        for r in (foreign.get("rows") or [])
        if isinstance(r, dict)
    )
    assert len(cortex.asks) == 1

    get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    get_settings.cache_clear()


def test_match_without_cortex_submit_does_not_stamp_l0(warehouse: Path) -> None:
    """Planted: restoring local execute_sql would go red if this stays None."""
    register_verified_query(space_id=FINANCE, question=QUESTION, sql=SQL, path=warehouse)
    env = maybe_verified_ask(
        QUESTION, space_id=FINANCE, warehouse=warehouse, session_id="ses_vq02_nsubmit"
    )
    assert env is None


def test_bind_shaped_submit_does_not_stamp_local_l0(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session-bind QueryResult has no SQL output — that must not mint VQ L0."""
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex(sql_output=None)
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    assert (
        client.post(
            "/v1/studio/verified-queries",
            json={"space_id": FINANCE, "question": QUESTION, "sql": SQL},
        ).status_code
        == 200
    )
    hit = client.post(
        "/v1/chat/ask",
        json={"question": QUESTION, "space_id": FINANCE, "session_id": "ses_vq02_bind"},
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env.get("sql_used") != SQL
    assert not any(
        str(r.get("name")) == "Warehouse A" and float(r.get("capacity_kg") or 0) == 100000.0
        for r in (env.get("rows") or [])
        if isinstance(r, dict)
    )
    assert cortex.asks, "failed VQ certify must fall through to Cortex ask"
    assert not cortex.appends
    get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    get_settings.cache_clear()


def test_ledger_without_hash_does_not_stamp_l0(
    warehouse: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F52(b): entry_id reused as hash is not a ledger signature."""
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex(append_entry_id="led_vq02", append_hash="led_vq02")
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    assert (
        client.post(
            "/v1/studio/verified-queries",
            json={"space_id": FINANCE, "question": QUESTION, "sql": SQL},
        ).status_code
        == 200
    )
    hit = client.post(
        "/v1/chat/ask",
        json={"question": QUESTION, "space_id": FINANCE, "session_id": "ses_vq02_led"},
    )
    assert hit.status_code == 200, hit.text
    env = hit.json()
    assert_envelope_valid(env)
    assert env.get("sql_used") != SQL
    assert env.get("audit_id") != "led_vq02"
    assert cortex.asks
    get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    get_settings.cache_clear()
