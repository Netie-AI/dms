"""ONTO-PG-01. In-memory tests always run. Postgres tests skip without DATABASE_URL.

CI has no Postgres service. A skip here is that fact, not a hidden pass.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from dms_core.control_plane.onto_data import (
    OntologyDataStore,
    PostgresOntologyDataStore,
    onto_pg_enabled,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_executor.ontology import Ontology

ROOT = Path(__file__).resolve().parents[1]
REV = ROOT / "alembic" / "versions" / "0008_ontology_approval_ref.py"
PREV = ROOT / "alembic" / "versions" / "0007_ontology_data.py"


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(str(sql))


def _load_revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rev0008_ontology_approval_ref", REV)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _body(*extra: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": "proposed",
        "served": False,
        "vendor_sha": "onto-pg-01",
        "terms": [
            {
                "name": "Customer",
                "table": "customers",
                "description": "buyer",
                "attributes": [],
                "key": ["id"],
            }
        ],
    }
    for kind, payload in extra:
        body.setdefault(kind, []).append(payload)
    return body


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


def test_migration_0008_adds_approval_ref_and_leaves_0007(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_ONTO_PG", raising=False)
    previous = PREV.read_text(encoding="utf-8")
    assert "approval_ref" not in previous
    assert 'revision: str = "0007_ontology_data"' in previous
    mod = _load_revision()
    assert mod.revision == "0008_ontology_approval_ref"
    assert mod.down_revision == "0007_ontology_data"
    rec = _Recorder()
    mod.op = rec
    mod.upgrade()
    upgrade_sql = "\n".join(rec.statements)
    rec.statements.clear()
    mod.downgrade()
    downgrade_sql = "\n".join(rec.statements)
    assert "ADD COLUMN approval_ref TEXT" in upgrade_sql
    assert "ALTER COLUMN approved_by TYPE TEXT" in upgrade_sql
    assert "DROP COLUMN IF EXISTS approval_ref" in downgrade_sql
    assert "password" not in upgrade_sql.lower()
    assert onto_pg_enabled() is False


def test_memory_approve_version_writes_actor_and_one_ref() -> None:
    store = OntologyDataStore()
    stored = store.store_proposal(
        _body(("measures", {"name": "orders", "expression": "count(*)"})),
        created_by="steward",
    )
    assert len(stored["items"]) == 2
    count = store.approve_version(stored["version_id"], "steward")
    assert count == 2
    approved = store.approved_items()
    assert {row["id"] for row in approved} == {row["id"] for row in stored["items"]}
    assert {row["approved_by"] for row in approved} == {"steward"}
    refs = {row["approval_ref"] for row in approved}
    assert len(refs) == 1
    assert next(iter(refs))
    assert store.approve_version(stored["version_id"], "steward") == 0
    other = store.store_proposal(_body(), created_by="steward")
    assert other["items"][0]["id"] not in {row["id"] for row in store.approved_items()}
    held = store.get_item(other["items"][0]["id"])
    assert held is not None and held.status == "proposed"
    diff = store.diff(other["version_id"])
    assert other["items"][0]["id"] in {row["id"] for row in diff["same"]}


def test_memory_approve_sets_approved_by_and_approval_ref() -> None:
    store = OntologyDataStore()
    stored = store.store_proposal(_body(), created_by="steward")
    item_id = stored["items"][0]["id"]
    row = store.approve(item_id, actor="steward")
    assert row is not None
    assert row.approved_by == "steward"
    assert row.approval_ref
    public = store.approved_items()[0]
    assert public["approved_by"] == "steward"
    assert public["approval_ref"] == row.approval_ref


def test_memory_approve_version_unknown_raises_and_empty_returns_zero() -> None:
    store = OntologyDataStore()
    with pytest.raises(KeyError):
        store.approve_version("missing-version", "steward")
    stored = store.store_proposal(
        {"status": "proposed", "served": False, "vendor_sha": "x", "terms": []},
        created_by="steward",
    )
    assert stored["items"] == []
    assert store.approve_version(stored["version_id"], "steward") == 0


def test_factory_stays_in_memory_until_the_flag_and_url_are_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_ONTO_PG", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert isinstance(ontology_data_store(), OntologyDataStore)
    monkeypatch.setenv("DATABASE_URL", "postgresql://dms@127.0.0.1:1/dms")
    assert isinstance(ontology_data_store(), OntologyDataStore)
    assert not isinstance(ontology_data_store(), PostgresOntologyDataStore)
    monkeypatch.setenv("DMS_ONTO_PG", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert isinstance(ontology_data_store(), OntologyDataStore)
    monkeypatch.setenv("DATABASE_URL", "postgresql://dms@127.0.0.1:1/dms")
    monkeypatch.setenv("DMS_TENANT_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    store = ontology_data_store()
    assert isinstance(store, PostgresOntologyDataStore)


def test_from_store_reads_through_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Store:
        def approved_items(self) -> list[dict[str, Any]]:
            seen.append("approved_items")
            return [
                {
                    "id": "item-1",
                    "kind": "object",
                    "status": "approved",
                    "payload": {
                        "name": "Customer",
                        "relation": "customers",
                        "key": ["id"],
                        "description": "",
                        "attributes": [],
                    },
                }
            ]

    monkeypatch.setattr(
        "dms_core.control_plane.onto_data.ontology_data_store",
        lambda: _Store(),
    )
    onto = Ontology.from_store()
    assert seen == ["approved_items"]
    assert "Customer" in onto.objects
    seen.clear()
    Ontology.from_store(items=[])
    assert seen == []


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def pg_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL is unset; Postgres ontology tests skipped")
    try:
        conn = psycopg.connect(url, connect_timeout=2)
    except Exception as exc:
        pytest.skip(f"DATABASE_URL is set but Postgres is unreachable ({type(exc).__name__})")
    else:
        conn.close()
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    return url


def _new_tenant(url: str) -> str:
    tenant = str(uuid.uuid4())
    slug = "onto-pg-" + tenant.split("-", 1)[0]
    conn = psycopg.connect(url, autocommit=True)
    try:
        conn.execute(
            "INSERT INTO dms.tenants (id, slug, name) VALUES (%s, %s, %s)",
            (tenant, slug, "onto-pg"),
        )
    finally:
        conn.close()
    return tenant


def _drop_tenant(url: str, tenant: str) -> None:
    conn = psycopg.connect(url, autocommit=True)
    try:
        conn.execute("DELETE FROM dms.tenants WHERE id = %s", (tenant,))
    finally:
        conn.close()


def test_postgres_approve_version_is_visible_on_a_later_connection(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _new_tenant(pg_url)
    connects = {"n": 0}
    real = PostgresOntologyDataStore._connect

    def _counting(self: PostgresOntologyDataStore) -> psycopg.Connection:
        connects["n"] += 1
        return real(self)

    monkeypatch.setattr(PostgresOntologyDataStore, "_connect", _counting)
    try:
        writer = PostgresOntologyDataStore(pg_url, tenant_id=tenant)
        approver = PostgresOntologyDataStore(pg_url, tenant_id=tenant)
        reader = PostgresOntologyDataStore(pg_url, tenant_id=tenant)
        actor = str(uuid.uuid4())
        stored = writer.store_proposal(_body(), created_by=actor, tenant_id=tenant)
        assert len(stored["items"]) == 1
        assert approver.approve_version(stored["version_id"], actor) == 1
        approved = reader.approved_items()
        assert connects["n"] == 3
        assert [row["id"] for row in approved] == [stored["items"][0]["id"]]
        assert approved[0]["status"] == "approved"
        assert approved[0]["approved_by"] == actor
        assert approved[0]["approval_ref"]
        assert approved[0]["payload"]["name"] == "Customer"
    finally:
        _drop_tenant(pg_url, tenant)


def test_postgres_approve_version_unknown_raises_and_empty_returns_zero(pg_url: str) -> None:
    tenant = _new_tenant(pg_url)
    try:
        store = PostgresOntologyDataStore(pg_url, tenant_id=tenant)
        with pytest.raises(KeyError):
            store.approve_version(str(uuid.uuid4()), str(uuid.uuid4()))
        stored = store.store_proposal(
            {"status": "proposed", "served": False, "vendor_sha": "x", "terms": []},
            created_by=str(uuid.uuid4()),
            tenant_id=tenant,
        )
        assert stored["items"] == []
        assert store.approve_version(stored["version_id"], "steward") == 0
    finally:
        _drop_tenant(pg_url, tenant)
