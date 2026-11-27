"""R1-LIVE-FIX-01. Ontology is the Space store. Space sources bind the uuid.

The sources 500 was ``list_sources(settings)`` (the FastAPI route). Its
``space_id`` default is a Query object, which is truthy, and ``UUID()`` then
raises ``AttributeError: 'Query' object has no attribute 'replace'`` only when
``DATABASE_URL`` is set. The helper receives the Space id string instead.

No flag. The old Cortex ``/dms/ontology`` proxy is not called.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_core.control_plane.onto_store import OntologyStore, SourceIdentity
from dms_core.control_plane.seed import SPACE_MARGIN, SPACE_Q3
from dms_executor.space_ontology import set_ontology_store
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contract" / "dms-ontology-read.schema.json"
FINANCE = str(SPACE_Q3)
OPS = str(SPACE_MARGIN)


def _smoke():
    path = ROOT / "scripts" / "verify" / "live_pages_smoke.py"
    spec = importlib.util.spec_from_file_location("live_pages_smoke", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    app = create_app()
    yield TestClient(app)
    get_settings.cache_clear()


@pytest.fixture
def empty_ontology():
    prev = set_ontology_store(OntologyStore())
    reset_ontology_data_store()
    try:
        yield
    finally:
        set_ontology_store(prev)
        reset_ontology_data_store()


def _assert_schema(body: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["required"]) <= set(body)
    assert set(body) <= set(schema["properties"])
    assert body["source"] in schema["properties"]["source"]["enum"]
    assert set(schema["properties"]["counts"]["required"]) <= set(body["counts"])


def test_both_seeded_spaces_sources_are_200(client: TestClient) -> None:
    for space_id in (FINANCE, OPS):
        res = client.get(f"/v1/spaces/{space_id}/sources")
        assert res.status_code == 200, space_id
        body = res.json()
        assert body["space_id"] == space_id
        assert body["count"] == len(body["sources"])
        assert all(row["space_id"] == space_id for row in body["sources"])
    finance = client.get(f"/v1/spaces/{FINANCE}/sources").json()
    assert finance["count"] >= 1


class _Conn:
    def __init__(self) -> None:
        self.params: tuple[Any, ...] | None = None

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def execute(self, _sql: str, params: tuple[Any, ...] | None = None) -> _Conn:
        self.params = params
        return self

    def fetchall(self) -> list[Any]:
        return []

    def commit(self) -> None:
        return None


def test_sources_sql_binds_the_space_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL is set. The bound parameter is the Space uuid, not Query."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:1/dms")
    get_settings.cache_clear()

    def _down(self: object) -> list[object]:
        raise RuntimeError("postgres probe down")

    monkeypatch.setattr("dms_core.control_plane.pg_spaces.PostgresSpaceStore.list_spaces", _down)
    conn = _Conn()
    monkeypatch.setattr("dms_api.routes.library.psycopg.connect", lambda *_a, **_k: conn)
    monkeypatch.setattr("dms_api.routes.library.set_tenant_context", lambda *_a, **_k: None)
    try:
        client = TestClient(create_app())
        res = client.get(f"/v1/spaces/{FINANCE}/sources")
        assert res.status_code == 200, res.text[:200]
        assert conn.params is not None
        assert isinstance(conn.params[1], UUID)
        assert str(conn.params[1]) == FINANCE
        assert type(conn.params[1]).__name__ != "Query"
    finally:
        get_settings.cache_clear()


def test_unknown_space_sources_stay_404(client: TestClient) -> None:
    assert client.get(f"/v1/spaces/{uuid4()}/sources").status_code == 404


def test_pack_fallback_matches_the_schema(
    client: TestClient, empty_ontology: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ontology must not call Cortex")

    monkeypatch.setattr("dms_api.cortex_read.cortex_get", boom)
    body = client.get("/v1/ontology").json()
    _assert_schema(body)
    assert body["ok"] is True
    assert body["source"] == "pack"
    assert body["space_id"] == FINANCE
    assert body["counts"]["object_types"] >= 1
    assert body["counts"]["metrics"] >= 1
    ops = client.get(f"/v1/ontology?space_id={OPS}").json()
    assert ops["source"] == "pack"
    assert ops["space_id"] == OPS
    for section in ("objects", "links", "actions", "functions", "metrics", "graph"):
        res = client.get(f"/v1/ontology/{section}")
        assert res.status_code == 200
        assert res.json()["source"] == "pack"
    assert client.get("/v1/ontology/secrets").status_code == 404


def test_graph_comes_from_the_finance_store(
    client: TestClient, empty_ontology: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ontology must not call Cortex")

    monkeypatch.setattr("dms_api.cortex_read.cortex_get", boom)
    store = OntologyStore()
    set_ontology_store(store)
    version = store.reconnect(
        space_id=UUID(FINANCE),
        identity=SourceIdentity(
            kind="postgresql", host="db.internal", database="finance", schema="public"
        ),
        fingerprint="live-fix-01",
    )
    store.record_snapshot(
        version.id,
        body={
            "objects": {
                "invoice": {
                    "relation": "finance.invoice",
                    "source_table": "public.invoice",
                    "key": ["id"],
                    "attributes": [{"name": "id", "type": "VARCHAR"}],
                },
                "customer": {
                    "relation": "finance.customer",
                    "source_table": "public.customer",
                    "key": ["id"],
                    "attributes": [{"name": "id", "type": "VARCHAR"}],
                },
            },
            "links": {
                "invoice_customer": {
                    "from": "invoice",
                    "from_columns": ["customer_id"],
                    "to": "customer",
                    "to_columns": ["id"],
                    "cardinality": "many_to_one",
                    "max_fanout": 1,
                }
            },
        },
        violations=[],
        verified=True,
    )
    body = client.get("/v1/ontology/graph").json()
    _assert_schema(body)
    assert body["source"] == "store"
    assert body["space_id"] == FINANCE
    assert {node["id"] for node in body["nodes"]} == {"invoice", "customer"}
    assert body["edges"][0]["source"] == "invoice"
    assert body["edges"][0]["target"] == "customer"
    other = client.get(f"/v1/ontology/graph?space_id={OPS}").json()
    assert other["source"] == "pack"
    assert other["nodes"]
    assert "invoice" not in {node["id"] for node in other["nodes"]}


def test_approved_items_are_the_store(client: TestClient, empty_ontology: None) -> None:
    stored = store_proposal(
        {
            "status": "proposed",
            "served": False,
            "vendor_sha": "live-fix-01",
            "terms": [
                {
                    "name": "Invoice",
                    "table": "invoices",
                    "description": "a bill",
                    "key": ["id"],
                    "attributes": [],
                }
            ],
            "declared_fks": [
                {
                    "source_table": "invoices",
                    "source_column": "customer_id",
                    "target_table": "customers",
                    "target_column": "id",
                }
            ],
            "measures": [{"name": "invoice_total"}],
        },
        created_by="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        space_id=FINANCE,
    )
    for item in stored["items"]:
        ontology_data_store().approve(item["id"], actor="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    body = client.get("/v1/ontology").json()
    _assert_schema(body)
    assert body["source"] == "store"
    assert "Invoice" in {row["id"] for row in body["object_types"]}
    assert any(row["id"] == "invoice_total" for row in body["metrics"])
    assert body["edges"]
    assert body["metrics"][0]["sql"] == ""


def test_page_load_gets_are_not_errors(client: TestClient, empty_ontology: None) -> None:
    smoke = _smoke()
    bad = smoke.failing_paths(lambda path: client.get(path).status_code)
    assert bad == [], bad
    assert smoke.self_check() == 0
