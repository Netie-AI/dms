"""CONNECT-GRANT-01: connect a source, grant its Space via the API, then ask.

The harness does not pass ``session_store`` and does not set ``extra_grants``.
The default session store reads the in-memory grant book the API wrote.
Table names here are synthetic. No warehouse fixture is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.control_plane.connect_grants import reset as reset_grants
from dms_executor.connectors import clear_connectors, register_connector
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

LEFT = "cg_left"
RIGHT = "cg_right"
MARKER = 7
OTHER = 11
QUESTION = "cg marker tally please"
TOKEN = "cg-harness-bearer"
SEEDED = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _holds(readable: set[str], table: str) -> bool:
    """Exact grant name, or schema.table from the SQL connector listing."""
    return table in readable or any(name.endswith("." + table) for name in readable)


def _allow(*, action: str, **_: Any) -> ComplianceDecision:
    return ComplianceDecision(allowed=True, reason="test_allow", action=action)


@dataclass
class _Cortex:
    """Answers only when the bound manifest names the grounded table."""

    asks: list[Any] = field(default_factory=list)
    _bound: dict[str, set[str]] = field(default_factory=dict)

    def submit(self, req: Any) -> QueryResult:
        self._bound[req.manifest.session_id] = set(req.manifest.row_predicates)
        return QueryResult(ok=True, status="bound", run_id="run-cg")

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        readable = self._bound.get(req.session_id, set())
        # live_ask prefixes the grounded table onto the question. Match the
        # manifest, not the original wording. A SQL listing uses schema.table.
        if _holds(readable, LEFT) and not _holds(readable, RIGHT):
            return AskResponse(
                answer=f"Connected marker is {MARKER}.",
                abstained=False,
                badge="certified",
                sql_used=f"SELECT marker FROM {LEFT}",
                rows=[{"marker": MARKER}],
                audit_id="aud-cg",
                route="sql",
            )
        if _holds(readable, RIGHT) and not _holds(readable, LEFT):
            return AskResponse(
                answer=f"Connected marker is {OTHER}.",
                abstained=False,
                badge="certified",
                sql_used=f"SELECT marker FROM {RIGHT}",
                rows=[{"marker": OTHER}],
                audit_id="aud-cg-right",
                route="sql",
            )
        return AskResponse(
            answer="ABSTAIN ungranted_table",
            abstained=True,
            badge="abstain",
            rows=[],
            route="refused",
            audit_id="aud-cg-no",
        )


def _minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-08-02T00:00:00+00:00",
            expires_at="2026-08-02T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


@pytest.fixture()
def harness(tmp_path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Cortex]:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "cg.duckdb"))
    monkeypatch.delenv("CORTEX_WAREHOUSE_DB", raising=False)
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_CCA_CASCADE", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import dms_api.app as app_mod
    from dms_api import settings as settings_mod
    from dms_api.routes import connect as connect_routes
    from dms_api.routes import spaces as spaces_routes
    from dms_api.routes import studio as studio_routes
    from dms_executor import Executor

    settings_mod.get_settings.cache_clear()
    reset_grants()
    clear_connectors()
    cortex = _Cortex()
    minter = _minter(monkeypatch)

    def _build(*_a: Any, **_k: Any) -> Executor:
        # Default session store. No extra_grants, no injected store.
        return Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "build_ask_service", _build)
    monkeypatch.setattr(connect_routes, "compliance_gate", _allow)
    monkeypatch.setattr(spaces_routes, "compliance_gate", _allow)
    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)

    app = app_mod.create_app()
    with TestClient(app) as client:
        store = client.app.state.ask_service._session_store
        assert isinstance(store, DemoSessionStore)
        assert store.extra_grants == ()
        yield client, cortex
    reset_grants()
    clear_connectors()
    settings_mod.get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _connect(
    client: TestClient,
    connector_id: str,
    tables: list[str],
    *,
    grant: list[str] | None,
    space_name: str,
) -> dict[str, Any]:
    register_connector(connector_id, lambda: list(tables))
    body: dict[str, Any] = {"connector_id": connector_id, "space_name": space_name}
    if grant is not None:
        body["grant_tables"] = grant
    res = client.post("/v1/connect/sources", headers=_auth(), json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _ask(
    client: TestClient, *, space_id: str, table: str
) -> Any:
    return client.post(
        "/v1/chat/ask",
        json={
            "question": QUESTION,
            "space_id": space_id,
            "session_id": "ses_cg",
            "grounded_tables": [table],
        },
    )


def test_harness_connects_grants_and_asks_without_session_injection(
    harness: tuple[TestClient, _Cortex],
) -> None:
    client, cortex = harness
    registered = _connect(
        client, "cg-src", [LEFT, RIGHT], grant=[LEFT], space_name="Cg one"
    )
    space_id = registered["space"]["id"]
    assert space_id != SEEDED
    assert registered["exposed_tables"] == [LEFT, RIGHT]
    assert registered["granted_tables"] == [LEFT]
    assert registered["persisted"] is False
    assert registered["storage"]["backend"] == "memory"
    exe = client.app.state.ask_service
    assert LEFT in exe.grantable_tables(space_id=space_id)
    assert LEFT not in exe.grantable_tables(space_id=None)

    res = _ask(client, space_id=space_id, table=LEFT)
    assert res.status_code == 200, res.text
    env = res.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"] == [{"marker": MARKER}]
    assert str(MARKER) in env["text"]
    assert LEFT in env["grounded_tables"]
    assert RIGHT not in env["grounded_tables"]
    assert cortex.asks, "the granted ask must reach the engine"


def test_ungranted_table_in_a_connected_source_is_denied(
    harness: tuple[TestClient, _Cortex],
) -> None:
    client, cortex = harness
    registered = _connect(
        client, "cg-src", [LEFT, RIGHT], grant=[LEFT], space_name="Cg gap"
    )
    res = _ask(client, space_id=registered["space"]["id"], table=RIGHT)
    assert res.status_code == 403, res.text
    body = res.json()
    assert body["detail"]["code"] == "grounding_not_grantable"
    assert RIGHT in body["detail"]["ungrantable_tables"]
    assert "rows" not in body
    assert str(OTHER) not in res.text
    assert cortex.asks == []


def test_unknown_space_is_denied(harness: tuple[TestClient, _Cortex]) -> None:
    client, cortex = harness
    missing = client.post(
        "/v1/chat/ask",
        json={
            "question": QUESTION,
            "space_id": "00000000-0000-0000-0000-000000000099",
            "grounded_tables": [LEFT],
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "space_not_found"

    created = client.post("/v1/spaces", json={"name": "Cg catalog only"})
    assert created.status_code == 201, created.text
    res = _ask(client, space_id=created.json()["space"]["id"], table=LEFT)
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "grounding_not_grantable"
    assert cortex.asks == []

    store = DemoSessionStore()
    assert store.is_space_member("no-such-space", "user") is False
    assert store.is_space_member(SEEDED, "user") is True


def test_second_space_cannot_read_the_first(
    harness: tuple[TestClient, _Cortex],
) -> None:
    client, _cortex = harness
    first = _connect(client, "cg-a", [LEFT], grant=[LEFT], space_name="Cg A")
    second = _connect(client, "cg-b", [RIGHT], grant=[RIGHT], space_name="Cg B")
    own = _ask(client, space_id=first["space"]["id"], table=LEFT)
    assert own.status_code == 200, own.text
    denied = _ask(client, space_id=second["space"]["id"], table=LEFT)
    assert denied.status_code == 403, denied.text
    assert str(MARKER) not in denied.text
    allowed = _ask(client, space_id=second["space"]["id"], table=RIGHT)
    assert allowed.status_code == 200, allowed.text
    env = allowed.json()
    assert_envelope_valid(env)
    assert env["rows"] == [{"marker": OTHER}]
    assert str(OTHER) in env["text"]
    assert env["abstained"] is False


def test_bad_token_cannot_grant(harness: tuple[TestClient, _Cortex]) -> None:
    client, _cortex = harness
    registered = _connect(
        client, "cg-src", [LEFT, RIGHT], grant=[LEFT], space_name="Cg token"
    )
    before = client.get("/v1/connect/audit", headers=_auth()).json()["rows"]
    res = client.post(
        f"/v1/connect/sources/{registered['source_id']}/grants",
        headers={"Authorization": "Bearer not-the-token"},
        json={"tables": [RIGHT]},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "bearer_denied"
    after = client.get("/v1/connect/audit", headers=_auth()).json()["rows"]
    assert after == before
    still = _ask(client, space_id=registered["space"]["id"], table=RIGHT)
    assert still.status_code == 403


def test_each_action_writes_an_audit_row(harness: tuple[TestClient, _Cortex]) -> None:
    client, _cortex = harness
    registered = _connect(
        client, "cg-src", [LEFT, RIGHT], grant=[LEFT], space_name="Cg audit"
    )
    revoked = client.post(
        f"/v1/connect/sources/{registered['source_id']}/revoke",
        headers=_auth(),
        json={"tables": [LEFT]},
    )
    assert revoked.status_code == 200, revoked.text
    rows = client.get("/v1/connect/audit", headers=_auth()).json()["rows"]
    actions = [row["action"] for row in rows]
    assert actions.count("register") == 1
    assert actions.count("grant") == 1
    assert actions.count("revoke") == 1
    for row in rows:
        assert row["actor"]
        assert row["token_id"] and row["token_id"] != TOKEN
        assert row["space_id"] == registered["space"]["id"]
        assert row["source_id"] == registered["source_id"]
        assert isinstance(row["tables"], list)
        assert row["at"]
        blob = str(row)
        assert TOKEN not in blob
        assert "marker" not in blob
    assert rows[0]["tables"] == [LEFT, RIGHT]
    assert rows[1]["tables"] == [LEFT]
    assert rows[2]["tables"] == [LEFT]


def test_sql_connector_bearer_registers_the_listing(
    harness: tuple[TestClient, _Cortex], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing POST /v1/studio/sources/sql, with the service bearer, grants the listing."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_db_connector import _FakeConnection, _install

    client, _cortex = harness
    secret = "cg-secret-value"
    con = _FakeConnection(
        [("cgsch", LEFT), ("cgsch", RIGHT)],
        {
            LEFT: (["marker"], [[str(MARKER)]]),
            RIGHT: (["marker"], [[str(OTHER)]]),
        },
    )
    _install(monkeypatch, con)
    res = client.post(
        "/v1/studio/sources/sql",
        headers=_auth(),
        json={
            "kind": "sqlserver",
            "host": "db.example.net",
            "database": "cgdb",
            "user": "reader",
            "password": secret,
            "tables": [LEFT],
        },
    )
    assert res.status_code == 200, res.text
    assert secret not in res.text
    body = res.json()
    connection = body["connection"]
    assert connection["exposed_tables"] == [f"cgsch.{LEFT}", f"cgsch.{RIGHT}"]
    assert connection["granted_tables"] == connection["exposed_tables"]
    audit = client.get("/v1/connect/audit", headers=_auth()).json()
    assert secret not in str(audit)
    assert "marker" not in str(audit)
    asked = _ask(client, space_id=connection["space"]["id"], table=f"cgsch.{LEFT}")
    assert asked.status_code == 200, asked.text
    env = asked.json()
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["rows"] == [{"marker": MARKER}]
    assert str(MARKER) in env["text"]
