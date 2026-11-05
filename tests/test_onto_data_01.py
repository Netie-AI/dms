"""ONTO-DATA-01. No skip, no xfail. CI runs this file inside ``pytest tests/``."""

from __future__ import annotations

import importlib.util
import os
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import duckdb
import psycopg
import pytest
from cortex_client.models import AskResponse
from cortex_contract.execution import Manifest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
from dms_executor.ao_compile import OntologyProposal
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import Ontology
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
REV_PATH = ROOT / "alembic" / "versions" / "0007_ontology_data.py"
PIN = "eb8dd0621d07db3033fe3fddc68f62ff72614610"


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(str(sql))


class _BoomGrains:
    def __iter__(self):  # noqa: ANN204
        raise AssertionError("SUPPLY_CHAIN_GRAINS")

    def __contains__(self, item: object) -> bool:
        raise AssertionError("SUPPLY_CHAIN_GRAINS")


def _load_revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rev0007_ontology_data", REV_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _proposal() -> OntologyProposal:
    return OntologyProposal(
        status="proposed",
        vendor_sha=PIN,
        terms=(
            {
                "name": "Customer",
                "table": "customers",
                "description": "buyer",
                "attributes": [],
                "key": ["id"],
            },
        ),
        attributes=(),
        inferred_fks=(),
        declared_fks=(),
        joins=(),
        served=False,
    )


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_client.gate import ComplianceDecision

    def _allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.ontology.compliance_gate", _allow)


def _install_compile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dms_executor.compile_source", lambda conn, **_: _proposal())
    monkeypatch.setattr(
        "dms_executor.warehouse_path",
        lambda: Path("/tmp/onto-data-01-missing/nope.duckdb"),
    )


def _clear_pg_ontology() -> None:
    """The flag-on store is durable. A test starts from an empty tenant."""
    if os.environ.get("DMS_ONTO_PG", "0").strip() != "1":
        return
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return
    from dms_core.control_plane.onto_data import _DEFAULT_TENANT
    from dms_core.control_plane.session import set_tenant_context

    tenant = os.environ.get("DMS_TENANT_ID", "").strip() or _DEFAULT_TENANT
    conn = psycopg.connect(url)
    try:
        set_tenant_context(conn, tenant, role="admin")
        conn.execute("DELETE FROM dms.ontology_items")
        conn.execute("DELETE FROM dms.ontology_versions")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    _clear_pg_ontology()
    yield
    reset_ontology_data_store()
    _clear_pg_ontology()


def test_migration_0007_upgrades_and_downgrades() -> None:
    assert REV_PATH.is_file()
    previous = (ROOT / "alembic" / "versions" / "0006_onto_snapshot.py").read_text(
        encoding="utf-8"
    )
    chunks = (ROOT / "alembic" / "versions" / "0003_document_chunks.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0006_onto_snapshot"' in previous
    assert 'revision: str = "0003_document_chunks"' in chunks
    mod = _load_revision()
    assert mod.revision == "0007_ontology_data"
    assert mod.down_revision == "0006_onto_snapshot"
    rec = _Recorder()
    mod.op = rec
    mod.upgrade()
    upgrade_sql = "\n".join(rec.statements)
    rec.statements.clear()
    mod.downgrade()
    downgrade_sql = "\n".join(rec.statements)
    assert "CREATE TABLE dms.ontology_versions" in upgrade_sql
    assert "CREATE TABLE dms.ontology_items" in upgrade_sql
    assert "CHECK (kind IN ('object', 'link', 'measure', 'grain', 'pii'))" in upgrade_sql
    assert "CHECK (status IN ('proposed', 'approved', 'revoked'))" in upgrade_sql
    for column in (
        "payload JSONB",
        "source TEXT",
        "provenance JSONB",
        "created_by",
        "approved_by",
        "ts ",
    ):
        assert column in upgrade_sql
    assert "DROP TABLE IF EXISTS dms.ontology_items CASCADE" in downgrade_sql
    assert "DROP TABLE IF EXISTS dms.ontology_versions CASCADE" in downgrade_sql
    assert not re.search(r"(?i)password|secret|api_key", upgrade_sql)
    rec.statements.clear()
    mod.upgrade()
    again = "\n".join(rec.statements)
    assert "CREATE TABLE dms.ontology_versions" in again
    assert "CREATE TABLE dms.ontology_items" in again


def test_proposed_never_served(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    _forbid_demo(monkeypatch)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    _install_compile(monkeypatch)
    res = client.post("/v1/ontology/data/compile")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "proposed"
    assert body["served"] is False
    assert body["items"]
    assert {row["status"] for row in body["items"]} == {"proposed"}
    onto = Ontology.from_store()
    assert onto.objects == {}
    assert onto.measures == {}
    assert onto.__dict__.get("_served_ids") == ()
    onto.supply_chain_catalog()


def test_approve_then_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    _forbid_demo(monkeypatch)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    _install_compile(monkeypatch)
    compiled = client.post("/v1/ontology/data/compile")
    assert compiled.status_code == 200, compiled.text
    item_id = compiled.json()["items"][0]["id"]
    diff = client.get("/v1/ontology/data/diff")
    assert diff.status_code == 200, diff.text
    assert item_id in {row["id"] for row in diff.json()["proposed_only"]}
    approved = client.post(f"/v1/ontology/data/items/{item_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    onto = Ontology.from_store()
    assert "Customer" in onto.objects
    assert onto.objects["Customer"].key == ("id",)
    assert item_id in onto.__dict__["_served_ids"]
    after = client.get("/v1/ontology/data/diff").json()
    assert item_id not in {row["id"] for row in after["proposed_only"]}


def test_revoke_removed_from_next_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    _forbid_demo(monkeypatch)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    _install_compile(monkeypatch)
    item_id = client.post("/v1/ontology/data/compile").json()["items"][0]["id"]
    assert client.post(f"/v1/ontology/data/items/{item_id}/approve").status_code == 200
    seen = _ask_ontologies(monkeypatch)
    assert seen and "Customer" in seen[0].objects
    assert client.post(f"/v1/ontology/data/items/{item_id}/revoke").status_code == 200
    seen = _ask_ontologies(monkeypatch)
    assert seen and "Customer" not in seen[0].objects
    assert Ontology.from_store().objects == {}


def test_ungranted_steward_is_403() -> None:
    client = TestClient(create_app())
    store = ontology_data_store()
    before_approved = {row["id"] for row in store.approved_items()}
    before_proposed = {row["id"] for row in store.diff()["proposed_only"]}
    store.deny_steward(get_settings().dms_actor_user_id)
    routes = (
        ("post", "/v1/ontology/data/compile"),
        ("get", "/v1/ontology/data/diff"),
        ("post", "/v1/ontology/data/items/missing/approve"),
        ("post", "/v1/ontology/data/items/missing/revoke"),
    )
    for method, path in routes:
        res = getattr(client, method)(path)
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == "steward_grant_required"
    assert {row["id"] for row in store.approved_items()} == before_approved
    assert {row["id"] for row in store.diff()["proposed_only"]} == before_proposed


def test_flag_off_load_verified_still_calls_demo_ontology(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    calls = {"n": 0}
    import dms_executor.generative_ask as gen

    def _wrapped(warehouse: Path) -> Ontology:
        calls["n"] += 1
        assert warehouse == path
        return Ontology()

    monkeypatch.setattr(gen, "demo_ontology", _wrapped)
    loaded = gen.load_verified_ontology(path)
    assert calls["n"] == 1
    assert loaded is not None and loaded.objects == {}


def _forbid_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Ontology:
        raise AssertionError("demo_ontology")

    monkeypatch.setattr("dms_executor.ontology.demo_ontology", _boom)
    monkeypatch.setattr("dms_executor.generative_ask.demo_ontology", _boom)
    monkeypatch.setattr("dms_executor.ontology.SUPPLY_CHAIN_GRAINS", _BoomGrains())


def _ask_ontologies(monkeypatch: pytest.MonkeyPatch) -> list[Ontology]:
    import dms_executor

    captured: list[Ontology] = []

    def _capture(*_a: Any, **kwargs: Any) -> None:
        onto = kwargs.get("ontology")
        assert kwargs.get("demo_ontology_allowed") is False
        assert isinstance(onto, Ontology)
        captured.append(onto)
        return None

    monkeypatch.setattr(dms_executor, "maybe_generative_ask", _capture)
    exe = dms_executor.Executor(cortex=_FakeCortex(), minter=_minter(monkeypatch))
    exe.live_ask("onto-data-01 probe question", session_id="ses_onto_data")
    return captured


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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


class _FakeCortex:
    def __init__(self) -> None:
        self.asks: list[Any] = []

    def submit(self, req: Any) -> Any:
        return req

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="no figure",
            badge="abstain",
            sql_used=None,
            rows=[],
            assumptions="fixture",
            audit_id="aud-onto-data",
            route="abstain",
        )
