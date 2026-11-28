"""FDE-SPACE-GRANT-01. A SQL land with a Space is a source, and it is not a grant.

``DMS_SQL_SOURCE_BIND`` defaults off. Off, the studio SQL receipt and the
Space source list stay as they are. On, the landed tables are proposed on
``dms.data_sources`` (the in-memory book when Postgres is parked) and none
of them are granted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_core.control_plane.connect_grants import reset as reset_grants
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install

SECRET = "rx-bind-secret"
TABLES = ("rx_sku", "rx_store", "rx_fill")


def _allow(*, action: str, **_: object) -> ComplianceDecision:
    return ComplianceDecision(allowed=True, reason="test_allow", action=action)


def _rows(name: str) -> tuple[list[str], list[list[object]]]:
    if name == "rx_sku":
        return ["sku_id", "label"], [["s1", "alpha"], ["s2", "beta"]]
    if name == "rx_store":
        return ["store_id", "label"], [["t1", "north"]]
    return ["fill_id", "sku_id"], [["f1", "s1"], ["f2", "s2"], ["f3", "s1"]]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "bind.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    from dms_api import settings as settings_mod
    from dms_api.routes import spaces as spaces_routes
    from dms_api.routes import studio as studio_routes

    settings_mod.get_settings.cache_clear()
    reset_grants()
    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    monkeypatch.setattr(spaces_routes, "compliance_gate", _allow)
    catalog = [("dbo", name) for name in TABLES]
    data = {name: _rows(name) for name in TABLES}
    _install(monkeypatch, _FakeConnection(catalog, data))
    app = create_app()
    with TestClient(app) as http:
        yield http
    reset_grants()
    settings_mod.get_settings.cache_clear()


def _space(http: TestClient) -> str:
    created = http.post("/v1/spaces", json={"name": "Rx bind"})
    assert created.status_code == 201, created.text
    return str(created.json()["space"]["id"])


def _land(http: TestClient, space_id: str) -> dict[str, object]:
    body = {
        "kind": "sqlserver",
        "host": "db.example.net",
        "database": "rx_chain",
        "user": "reader",
        "password": SECRET,
        "tables": list(TABLES),
        "space_id": space_id,
    }
    res = http.post("/v1/studio/sources/sql", json=body)
    assert res.status_code == 200, res.text
    assert SECRET not in res.text
    return res.json()


def test_flag_off_does_not_register_a_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_SQL_SOURCE_BIND", raising=False)
    space_id = _space(client)
    landed = _land(client, space_id)
    assert "source_id" not in landed
    assert "granted_tables" not in landed
    listed = client.get(f"/v1/spaces/{space_id}/sources")
    assert listed.status_code == 200, listed.text
    assert listed.json()["count"] == 0
    assert SECRET not in listed.text


def test_flag_on_registers_one_source_and_grants_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SQL_SOURCE_BIND", "1")
    space_id = _space(client)
    landed = _land(client, space_id)
    assert landed["granted_tables"] == []
    assert len(landed["exposed_tables"]) == 3
    source_id = landed["source_id"]
    assert isinstance(source_id, str) and source_id
    listed = client.get(f"/v1/spaces/{space_id}/sources")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1
    assert body["sources"][0]["id"] == source_id
    assert body["sources"][0]["kind"] == "sql"
    assert body["sources"][0]["space_id"] == space_id
    assert body["sources"][0]["granted_tables"] == []
    assert SECRET not in listed.text
    assert "rx-bind-secret" not in str(body["sources"][0]["ref"])


def test_flag_off_grant_route_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SQL_SOURCE_BIND", raising=False)
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    http = TestClient(create_app())
    res = http.post(
        "/v1/studio/sources/src-missing/grants",
        json={"tables": ["bronze.dbo_rx_sku"]},
    )
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}


def test_flag_off_openapi_is_flag_on_minus_the_grant_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    import json

    from dms_api import settings as settings_mod

    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    monkeypatch.setenv("DMS_SQL_SOURCE_BIND", "1")
    settings_mod.get_settings.cache_clear()
    on = create_app().openapi()
    monkeypatch.delenv("DMS_SQL_SOURCE_BIND", raising=False)
    settings_mod.get_settings.cache_clear()
    off = create_app().openapi()
    new_path = "/v1/studio/sources/{source_id}/grants"
    assert new_path in on["paths"]
    assert new_path not in off["paths"]
    new_paths = {new_path}
    stripped = copy.deepcopy(on)
    for path in new_paths:
        del stripped["paths"][path]
    stripped["components"]["schemas"].pop("SqlBindGrantIn", None)
    assert json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def _ungranted(env: dict[str, object]) -> bool:
    blob = " ".join(
        str(env.get(key) or "")
        for key in ("text", "abstain_reason", "assumptions")
    )
    return "ungranted" in blob


def _ask(
    http: TestClient, *, space_id: str, question: str, path: str
) -> dict[str, object]:
    from uuid import uuid4

    res = http.post(
        "/v1/chat/ask",
        json={
            "question": question,
            "space_id": space_id,
            "session_id": f"ses_{uuid4().hex[:12]}",
            "ask_path": path,
        },
    )
    assert res.status_code == 200, res.text
    env = res.json()
    assert SECRET not in res.text
    return env


@pytest.fixture()
def asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """App whose ask service is a scripted model. The model writes the SQL."""
    from dataclasses import dataclass, field
    from typing import Any

    import dms_api.app as app_mod
    from cortex_client.models import AskResponse
    from cortex_contract.execution import Manifest, QueryResult
    from dms_api import settings as settings_mod
    from dms_api.routes import chat as chat_routes
    from dms_api.routes import spaces as spaces_routes
    from dms_api.routes import studio as studio_routes
    from dms_api.routes import studio_sql_bind
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter, SessionAcl

    path = tmp_path / "ask.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_CCA_CASCADE", "0")
    monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    monkeypatch.setenv("DMS_RULES_OFF", "1")
    monkeypatch.setenv("DMS_SQL_SOURCE_BIND", "1")
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    settings_mod.get_settings.cache_clear()
    reset_grants()

    @dataclass
    class _Cortex:
        asks: list[Any] = field(default_factory=list)
        submits: list[str] = field(default_factory=list)

        def submit(self, req: Any) -> QueryResult:
            kind = (getattr(req, "plan", None) or {}).get("kind")
            if kind == "sql":
                sql = str((getattr(req, "body", None) or {}).get("sql") or "")
                self.submits.append(sql)
                return QueryResult(
                    ok=True, status="ok", run_id="run-sql", output={"rows": [{"n": 2}]}
                )
            return QueryResult(ok=True, status="bound", run_id="run-bind")

        def ask(self, req: Any) -> AskResponse:
            self.asks.append(req)
            question = str(getattr(req, "question", "") or "")
            table = ""
            marker = "contract count "
            if marker in question:
                table = question.split(marker, 1)[1].strip().split()[0]
            sql = f"SELECT COUNT(*) AS n FROM {table}" if table else "SELECT 1 AS n"
            return AskResponse(
                answer="Row count is 2.",
                abstained=False,
                badge="certified",
                sql_used=sql,
                rows=[{"n": 2}],
                audit_id="aud-bind",
                route="sql",
            )

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any] | None:
            marker = "generate count "
            if marker not in question:
                return None
            table = question.split(marker, 1)[1].strip().split()[0]
            return {
                "query_sql": f"SELECT COUNT(*) AS n FROM {table}",
                "plan_source": "ontology_plan",
            }

        def ledger_append(self, _req: Any) -> Any:
            return type("Ledger", (), {"entry_id": "e-bind", "seq": 1, "hash": "h-bind"})()

    cortex = _Cortex()
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
            issued_at="2026-11-28T00:00:00+00:00",
            expires_at="2026-11-28T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)

    def _build(*_a: Any, **_k: Any) -> Executor:
        return Executor(cortex=cortex, minter=minter, warehouse_path=path)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "build_ask_service", _build)
    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    monkeypatch.setattr(spaces_routes, "compliance_gate", _allow)
    monkeypatch.setattr(chat_routes, "compliance_gate", _allow)
    monkeypatch.setattr(studio_sql_bind, "compliance_gate", _allow)
    catalog = [("dbo", name) for name in TABLES]
    data = {name: _rows(name) for name in TABLES}
    _install(monkeypatch, _FakeConnection(catalog, data))
    app = create_app()
    with TestClient(app) as http:
        yield http, cortex
    reset_grants()
    settings_mod.get_settings.cache_clear()


def _names(landed: dict[str, object]) -> list[str]:
    exposed = landed.get("exposed_tables")
    if isinstance(exposed, list) and exposed:
        return [str(name) for name in exposed]
    tables = landed.get("tables")
    assert isinstance(tables, list)
    return [str(row["bronze_table"]) for row in tables]


def test_flag_off_grounds_every_landed_table(asked) -> None:
    """Today's Cortex path. Flag off, the three bronze tables stay grounded."""
    import os

    http, _cortex = asked
    os.environ["DMS_SQL_SOURCE_BIND"] = "0"
    space_id = _space(http)
    landed = _land(http, space_id)
    names = _names(landed)
    assert "source_id" not in landed
    exe = http.app.state.ask_service
    granted = set(exe.grantable_tables(space_id=space_id))
    assert set(names) <= granted
    env = _ask(http, space_id=space_id, question=f"contract count {names[0]}", path="product")
    assert env["abstained"] is False
    assert env["rows"] == [{"n": 2}]
    assert "2" in str(env["text"])
    grounded = set(env["grounded_tables"])
    assert set(names) <= grounded


def test_both_paths_abstain_until_the_steward_grants(asked) -> None:
    http, _cortex = asked
    space_id = _space(http)
    landed = _land(http, space_id)
    names = _names(landed)
    sku, store, fill = names
    exe = http.app.state.ask_service
    assert exe.grantable_tables(space_id=space_id) == []
    for path in ("product", "generative"):
        env = _ask(
            http,
            space_id=space_id,
            question=f"contract count {sku}",
            path=path,
        )
        assert env["abstained"] is True
        assert _ungranted(env)
        assert set(env.get("grounded_tables") or []).isdisjoint(names)
        assert env.get("rows") in ([], None)

    granted = http.post(
        f"/v1/studio/sources/{landed['source_id']}/grants",
        json={"tables": [sku, store]},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["granted_tables"] == [sku, store]
    assert SECRET not in granted.text
    readable = set(exe.grantable_tables(space_id=space_id))
    assert sku in readable and store in readable and fill not in readable

    product = _ask(
        http, space_id=space_id, question=f"contract count {sku}", path="product"
    )
    assert product["abstained"] is False
    assert product["rows"] == [{"n": 2}]
    assert "2" in str(product["text"])
    product_ground = set(product["grounded_tables"])
    assert sku in product_ground and store in product_ground
    assert fill not in product_ground

    denied = _ask(
        http, space_id=space_id, question=f"contract count {fill}", path="product"
    )
    assert denied["abstained"] is True
    assert _ungranted(denied)
    assert fill not in set(denied.get("grounded_tables") or [])

    generated = _ask(
        http, space_id=space_id, question=f"generate count {store}", path="generative"
    )
    assert generated["abstained"] is False, generated
    assert generated["rows"] == [{"n": 2}]
    assert "2" in str(generated["text"])
    assert fill not in set(generated.get("grounded_tables") or [])

    third = _ask(
        http, space_id=space_id, question=f"generate count {fill}", path="generative"
    )
    assert third["abstained"] is True, third
    # The generative checker names the same fact as the contract path's
    # ``ungranted``: this relation is not in the Space grant.
    blob = " ".join(
        str(third.get(key) or "")
        for key in ("text", "abstain_reason", "assumptions")
    )
    assert "sql_relation_not_granted" in blob or "ungranted" in blob
    assert third.get("rows") in ([], None)
    assert fill not in set(third.get("grounded_tables") or [])
